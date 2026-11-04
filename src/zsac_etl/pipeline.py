"""Orquestador: descubre archivos, decide cuáles procesar y carga cada uno de forma aislada."""

import calendar
import logging
import time
import traceback
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from zsac_etl import carga, control
from zsac_etl.config import cargar_columnas, cargar_config
from zsac_etl.db import obtener_conexion
from zsac_etl.extraccion import (
    ArchivoBloqueado,
    ArchivoRechazado,
    calcular_sha256,
    copiar_con_reintentos,
    descubrir_archivos,
    info_archivo,
    leer_archivo,
)
from zsac_etl.transformacion import transformar

logger = logging.getLogger("zsac_etl")

SALIDA = {"OK": 0, "PARCIAL": 1, "ERROR": 2}
SALIDA_BLOQUEADA = 3


@dataclass
class ResumenCorrida:
    corrida_id: str
    cargados: list = field(default_factory=list)      # (nombre, periodo, conteos)
    fallidos: list = field(default_factory=list)      # (nombre, periodo, estado, motivo)
    sin_cambios: int = 0
    errores_globales: list = field(default_factory=list)

    @property
    def estado(self):
        return estado_corrida(len(self.cargados), len(self.fallidos), bool(self.errores_globales))

    def total(self, clave):
        return sum(c[2].get(clave, 0) for c in self.cargados)

    def rango_fechas(self):
        periodos = [p for _, p, *_ in self.cargados + self.fallidos if p]
        if not periodos:
            hoy = date.today()
            return hoy, hoy
        hasta = max(periodos)
        return min(periodos), hasta.replace(day=calendar.monthrange(hasta.year, hasta.month)[1])

    def detalle(self):
        lineas = [f"corrida_id={self.corrida_id}", f"archivos cargados={len(self.cargados)}, "
                  f"sin cambios={self.sin_cambios}, fallidos={len(self.fallidos)}"]
        rechazos = Counter()
        for nombre, _, c in self.cargados:
            rechazos.update({"ERROR": c["error"], "ADVERTENCIA": c["advertencia"], "DUPLICADO": c["duplicadas"]})
            lineas.append(f"OK {nombre}: leidas={c['leidas']} sin_factura={c['sin_factura']} "
                          f"ins={c['insertadas']} act={c['actualizadas']} err={c['error']} "
                          f"adv={c['advertencia']} dup={c['duplicadas']}")
        for nombre, _, estado, motivo in self.fallidos:
            lineas.append(f"{estado} {nombre}: {motivo}")
        lineas.append(f"sin factura real={self.total('sin_factura')}; rechazos por tipo={dict(rechazos)}")
        lineas.extend(self.errores_globales)
        return "\n".join(lineas)


def estado_corrida(cargados, fallidos, error_global=False):
    """OK si no falló nada; PARCIAL si hubo fallas pero algo se cargó; ERROR si no se cargó nada."""
    if error_global and not cargados:
        return "ERROR"
    if fallidos == 0 and not error_global:
        return "OK"
    return "PARCIAL" if cargados else "ERROR"


def _seleccionar(fuentes, todos, archivo):
    if archivo:
        elegidas = [f for f in fuentes if f.nombre.casefold() == archivo.casefold()]
        if not elegidas:
            raise ValueError(f"No se encontró el archivo {archivo} en la carpeta de origen")
        return elegidas, True
    return fuentes, todos


def _procesar_archivo(conexion, cfg, columnas, fuente, copia, sha, resumen):
    tablas = cfg["tablas"]
    inicio = time.monotonic()
    logger.info("Procesando %s", fuente.nombre)
    try:
        extraido = leer_archivo(copia, fuente, columnas, cfg["origen"].get("hojas", ("BD", "DB")))
        for adv in extraido.advertencias:
            logger.warning("%s: %s", fuente.nombre, adv)
        logger.info("%s: hoja %s, %s filas leídas", fuente.nombre, extraido.hoja, len(extraido.datos))

        transformado = transformar(extraido.datos, columnas, cfg["reglas"]["factura_min_digitos"])
        if transformado.fechas_vacias_sap:
            logger.info("%s: %s fechas vacías de SAP cargadas como NULL", fuente.nombre, transformado.fechas_vacias_sap)
        tamano, fecha_mod = info_archivo(fuente.ruta)
        conteos = carga.cargar_archivo(
            conexion, tablas, resumen.corrida_id, fuente, transformado,
            filas_leidas=len(extraido.datos), hoja=extraido.hoja, hash_sha256=sha,
            tamano_bytes=tamano, fecha_modificacion=fecha_mod, advertencias_archivo=extraido.advertencias,
        )
        resumen.cargados.append((fuente.nombre, fuente.periodo, conteos))
        logger.info(
            "%s: sin factura real=%s, errores=%s, advertencias=%s, duplicadas=%s, insertadas=%s, "
            "actualizadas=%s (%.1fs)", fuente.nombre, conteos["sin_factura"], conteos["error"],
            conteos["advertencia"], conteos["duplicadas"], conteos["insertadas"], conteos["actualizadas"],
            time.monotonic() - inicio,
        )
    except ArchivoRechazado as e:
        _registrar_falla(conexion, tablas, fuente, "RECHAZADO", str(e), resumen)
    except Exception as e:
        logger.error("%s: error al procesar: %s", fuente.nombre, e)
        _registrar_falla(conexion, tablas, fuente, "ERROR", traceback.format_exc(), resumen)


def _registrar_falla(conexion, tablas, fuente, estado, motivo, resumen):
    if estado != "ERROR":
        logger.warning("%s: %s (%s)", fuente.nombre, estado, motivo)
    resumen.fallidos.append((fuente.nombre, fuente.periodo, estado, motivo.strip().splitlines()[-1]))
    try:
        conexion.rollback()
        carga.registrar_archivo(conexion, tablas, fuente.nombre, fuente.periodo, estado,
                                resumen.corrida_id, detalle=motivo[:4000])
        conexion.commit()
    except Exception as e:
        logger.error("No se pudo registrar el estado de %s: %s", fuente.nombre, e)


def ejecutar(todos=False, archivo=None, cfg=None):
    """Corrida completa. Devuelve el código de salida del proceso."""
    cfg = cfg or cargar_config()
    log_cfg = cfg.get("logging", {})
    control.configurar_logging(log_cfg.get("carpeta", "logs"), log_cfg.get("nivel", "INFO"),
                               log_cfg.get("rotacion_dias", 30))
    tablas, origen = cfg["tablas"], cfg["origen"]
    resumen = ResumenCorrida(corrida_id=str(uuid.uuid4()))
    modo = f"archivo {archivo}" if archivo else ("todos" if todos else "incremental")
    logger.info("Inicio de corrida %s (modo %s)", resumen.corrida_id, modo)

    try:
        conexion = obtener_conexion(cfg)
    except Exception as e:
        logger.error("No se pudo conectar a SQL Server: %s", e)
        return SALIDA["ERROR"]

    try:
        if not control.tomar_bloqueo(conexion):
            logger.warning("Ya hay otra corrida en curso; esta termina sin procesar nada")
            return SALIDA_BLOQUEADA

        try:
            columnas = cargar_columnas()
            fuentes, forzar = _seleccionar(descubrir_archivos(origen["carpeta"]), todos, archivo)
            ya_cargados = {} if forzar else carga.hashes_cargados(conexion, tablas)
            logger.info("%s archivos ZSAC encontrados", len(fuentes))

            for fuente in fuentes:
                try:
                    copia = copiar_con_reintentos(fuente.ruta, origen.get("carpeta_temporal") or None,
                                                  origen.get("reintentos", 3), origen.get("espera_segundos", 10))
                except ArchivoBloqueado as e:
                    _registrar_falla(conexion, tablas, fuente, "OMITIDO", str(e), resumen)
                    continue
                try:
                    sha = calcular_sha256(copia)
                    if ya_cargados.get(fuente.nombre) == sha:
                        resumen.sin_cambios += 1
                        logger.debug("%s sin cambios", fuente.nombre)
                        continue
                    _procesar_archivo(conexion, cfg, columnas, fuente, copia, sha, resumen)
                finally:
                    try:
                        copia.unlink(missing_ok=True)
                    except OSError:
                        logger.debug("No se pudo borrar la copia temporal %s", copia)
        except Exception as e:
            logger.error("Error general de la corrida: %s", e)
            resumen.errores_globales.append(traceback.format_exc())

        estado = resumen.estado
        desde, hasta = resumen.rango_fechas()
        try:
            control.registrar_corrida(
                conexion, tablas["control"], tablas["nombre_proceso"], desde, hasta,
                filas_staging=resumen.total("staging"), filas_insertadas=resumen.total("insertadas"),
                filas_actualizadas=resumen.total("actualizadas"), estado=estado, detalle=resumen.detalle(),
            )
        except Exception as e:
            logger.error("No se pudo registrar la corrida en %s: %s", tablas["control"], e)
            estado = "ERROR" if estado == "OK" else estado

        logger.info("Fin de corrida %s: %s (cargados=%s, sin cambios=%s, fallidos=%s, insertadas=%s, "
                    "actualizadas=%s)", resumen.corrida_id, estado, len(resumen.cargados), resumen.sin_cambios,
                    len(resumen.fallidos), resumen.total("insertadas"), resumen.total("actualizadas"))
        return SALIDA[estado]
    finally:
        control.liberar_bloqueo(conexion)
        conexion.close()


def crear_estructuras(cfg=None):
    cfg = cfg or cargar_config()
    control.configurar_logging(cfg.get("logging", {}).get("carpeta", "logs"))
    conexion = obtener_conexion(cfg)
    try:
        carga.crear_estructuras(conexion, cfg["tablas"])
        logger.info("Estructuras de base de datos creadas/verificadas")
    finally:
        conexion.close()
    return 0
