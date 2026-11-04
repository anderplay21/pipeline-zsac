"""Línea de comandos: python -m zsac_etl {run,init-db}."""

import argparse
import sys

from zsac_etl import pipeline


def construir_parser():
    parser = argparse.ArgumentParser(prog="zsac_etl", description="ETL de reportes ZSAC hacia SQL Server")
    sub = parser.add_subparsers(dest="comando", required=True)

    run = sub.add_parser("run", help="Ejecuta una corrida (por defecto, solo archivos nuevos o modificados)")
    grupo = run.add_mutually_exclusive_group()
    grupo.add_argument("--todos", action="store_true", help="Reprocesa todos los archivos")
    grupo.add_argument("--archivo", metavar="NOMBRE", help="Procesa solo ese archivo (p. ej. ZSAC_MAYO_2026.xlsx)")

    sub.add_parser("init-db", help="Crea o verifica las tablas en SQL Server (idempotente)")
    return parser


def main(argv=None):
    args = construir_parser().parse_args(argv)
    if args.comando == "init-db":
        return pipeline.crear_estructuras()
    return pipeline.ejecutar(todos=args.todos, archivo=args.archivo)


if __name__ == "__main__":
    sys.exit(main())
