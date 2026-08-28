"""
Datos dummy para poder probar el flujo de disponibilidad end-to-end antes de
tener el catálogo real de la CPA cargado. Se ejecuta con:

    flask --app wsgi.py seed-recursos

Es idempotente: si ya hay recursos cargados, no hace nada (para no duplicar
ni pisar datos reales una vez que se cargue el catálogo de verdad).
"""

from datetime import datetime, timedelta, timezone

import click
from flask import current_app

from app.extensions import db
from app.models import Recurso, Reserva, Usuario

RECURSOS_DUMMY = [
    dict(tipo="equipo", nombre="Cámara Sony A7III", descripcion="Mirrorless full frame",
         placa="10000001", categoria="cámara", marca="Sony", modelo="A7III", identificador="SN-A7III-001",
         requiere_seguro=True),
    dict(tipo="equipo", nombre="Cámara Canon EOS R", descripcion="Mirrorless full frame",
         placa="10000002", categoria="cámara", marca="Canon", modelo="EOS R", identificador="SN-EOSR-002",
         requiere_seguro=True),
    dict(tipo="equipo", nombre="Trípode Manfrotto", descripcion="Trípode de video con cabezal fluido",
         placa="10000003", categoria="trípode", marca="Manfrotto", modelo="MT055XPRO3", identificador=None,
         requiere_seguro=False),
    dict(tipo="equipo", nombre="Micrófono Rode NTG3", descripcion="Micrófono shotgun",
         placa="10000004", categoria="micrófono", marca="Rode", modelo="NTG3", identificador=None,
         requiere_seguro=False),
    dict(tipo="equipo", nombre="Kit de luces LED", descripcion="Kit de 2 paneles LED con soportes",
         placa="10000005", categoria="luces", marca="Neewer", modelo="660 PRO", identificador=None,
         requiere_seguro=True),
    dict(tipo="equipo", nombre="Dron DJI Mavic", descripcion="Dron con cámara 4K",
         placa="10000006", categoria="dron", marca="DJI", modelo="Mavic 3", identificador="IMEI-864012345678901",
         requiere_seguro=True),
    dict(tipo="espacio", nombre="Estudio de grabación A", descripcion="Estudio con croma",
         ubicacion="Edificio CPA, piso 2"),
    dict(tipo="espacio", nombre="Sala de edición 1", descripcion="Estación de edición con monitores calibrados",
         ubicacion="Edificio CPA, piso 1"),
]


@click.command("seed-recursos")
def seed_recursos_cmd():
    """Carga activos dummy y un par de reservas de ejemplo para poder probar
    los tres casos: recurso en uso ahora, recurso con reserva próxima, y
    recurso libre."""
    if Recurso.query.count() > 0:
        click.echo("Ya hay recursos cargados, no se toca nada.")
        return

    recursos = [Recurso(**data) for data in RECURSOS_DUMMY]
    db.session.add_all(recursos)
    db.session.flush()  # asigna ids sin cerrar la transacción

    usuario_prueba = Usuario(
        nombre="Usuario de Pruebas",
        telefono_wa="000000000000",
        codigo_uninorte="TEST-001",
        rol="estudiante",
    )
    db.session.add(usuario_prueba)
    db.session.flush()

    ahora = datetime.now(timezone.utc)
    por_nombre = {r.nombre: r for r in recursos}

    reserva_en_curso = Reserva(
        usuario_id=usuario_prueba.id,
        recurso_id=por_nombre["Cámara Sony A7III"].id,
        inicio=ahora - timedelta(minutes=30),
        fin=ahora + timedelta(minutes=30),
        estado="confirmada",
    )
    reserva_futura = Reserva(
        usuario_id=usuario_prueba.id,
        recurso_id=por_nombre["Estudio de grabación A"].id,
        inicio=ahora + timedelta(hours=2),
        fin=ahora + timedelta(hours=4),
        estado="confirmada",
    )
    db.session.add_all([reserva_en_curso, reserva_futura])
    db.session.commit()

    click.echo(f"Creados {len(recursos)} recursos dummy.")
    click.echo("- 'Cámara Sony A7III' queda EN USO ahora mismo (hasta dentro de 30 min).")
    click.echo("- 'Estudio de grabación A' queda libre ahora pero con reserva en 2h.")
    click.echo("- El resto quedan libres y sin reservas.")


def registrar_cli(app):
    app.cli.add_command(seed_recursos_cmd)
