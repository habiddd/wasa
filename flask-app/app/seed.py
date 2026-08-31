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
         placa="10000010", categoria="cámara", marca="Sony", modelo="A7III", identificador="SN-A7III-001",
         requiere_seguro=True),
    dict(tipo="equipo", nombre="Cámara Canon EOS R", descripcion="Mirrorless full frame",
         placa="10000020", categoria="cámara", marca="Canon", modelo="EOS R", identificador="SN-EOSR-002",
         requiere_seguro=True),
    dict(tipo="equipo", nombre="Cámara Canon EOS R6", descripcion="Mirrorless full frame",
         placa="10000070", categoria="cámara", marca="Canon", modelo="EOS R6", identificador="SN-EOSR6-007",
         requiere_seguro=True),
    dict(tipo="equipo", nombre="Trípode Manfrotto", descripcion="Trípode de video con cabezal fluido",
         placa="10000030", categoria="trípode", marca="Manfrotto", modelo="MT055XPRO3", identificador=None,
         requiere_seguro=False),
    dict(tipo="equipo", nombre="Micrófono Rode NTG3", descripcion="Micrófono shotgun",
         placa="10000040", categoria="micrófono", marca="Rode", modelo="NTG3", identificador=None,
         requiere_seguro=False),
    dict(tipo="equipo", nombre="Kit de luces LED", descripcion="Kit de 2 paneles LED con soportes",
         placa="10000050", categoria="luces", marca="Neewer", modelo="660 PRO", identificador=None,
         requiere_seguro=True),
    dict(tipo="equipo", nombre="Dron DJI Mavic", descripcion="Dron con cámara 4K",
         placa="10000060", categoria="dron", marca="DJI", modelo="Mavic 3", identificador="IMEI-864012345678901",
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

    # Dos lentes hijos de la Sony A7III, para poder probar el carrito con
    # accesorios por defecto (ver recursos_service._accesorios_por_defecto).
    camara_sony = next(r for r in recursos if r.nombre == "Cámara Sony A7III")
    lentes_sony = [
        Recurso(
            tipo="equipo", nombre="Lente Sony 24-70mm f/2.8", descripcion="Zoom estándar",
            placa="LEN0022", categoria="lente", marca="Sony", modelo="FE 24-70mm f/2.8 GM",
            padre_id=camara_sony.id, incluido_por_defecto=True,
        ),
        Recurso(
            tipo="equipo", nombre="Lente Sony 70-200mm f/2.8", descripcion="Zoom telefoto",
            placa="LEN0023", categoria="lente", marca="Sony", modelo="FE 70-200mm f/2.8 GM",
            padre_id=camara_sony.id, incluido_por_defecto=True,
        ),
    ]
    db.session.add_all(lentes_sony)
    db.session.flush()
    recursos += lentes_sony

    # Dos lentes hijos de la Canon EOS R y tres de la Canon EOS R6, para
    # probar el carrito con más de un padre/hijo y con bundles de tamaño
    # distinto (2 vs. 3 accesorios).
    camara_canon_r = next(r for r in recursos if r.nombre == "Cámara Canon EOS R")
    camara_canon_r6 = next(r for r in recursos if r.nombre == "Cámara Canon EOS R6")
    lentes_canon = [
        Recurso(
            tipo="equipo", nombre="Lente Canon RF 24-105mm f/4L", descripcion="Zoom estándar",
            placa="LEN0024", categoria="lente", marca="Canon", modelo="RF 24-105mm f/4L",
            padre_id=camara_canon_r.id, incluido_por_defecto=True,
        ),
        Recurso(
            tipo="equipo", nombre="Lente Canon RF 50mm f/1.2L", descripcion="Fijo retrato",
            placa="LEN0025", categoria="lente", marca="Canon", modelo="RF 50mm f/1.2L",
            padre_id=camara_canon_r.id, incluido_por_defecto=True,
        ),
        Recurso(
            tipo="equipo", nombre="Lente Canon RF 16-35mm f/2.8L", descripcion="Gran angular",
            placa="LEN0026", categoria="lente", marca="Canon", modelo="RF 16-35mm f/2.8L",
            padre_id=camara_canon_r6.id, incluido_por_defecto=True,
        ),
        Recurso(
            tipo="equipo", nombre="Lente Canon RF 70-200mm f/2.8L", descripcion="Zoom telefoto",
            placa="LEN0027", categoria="lente", marca="Canon", modelo="RF 70-200mm f/2.8L",
            padre_id=camara_canon_r6.id, incluido_por_defecto=True,
        ),
        Recurso(
            tipo="equipo", nombre="Lente Canon RF 100-500mm f/4.5-7.1L", descripcion="Supertelefoto",
            placa="LEN0028", categoria="lente", marca="Canon", modelo="RF 100-500mm f/4.5-7.1L",
            padre_id=camara_canon_r6.id, incluido_por_defecto=True,
        ),
    ]
    db.session.add_all(lentes_canon)
    db.session.flush()
    recursos += lentes_canon

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
    click.echo("- La Sony A7III trae 2 lentes hijos incluidos por defecto (LEN0022, LEN0023).")
    click.echo("- La Canon EOS R trae 2 lentes hijos incluidos por defecto (LEN0024, LEN0025).")
    click.echo("- La Canon EOS R6 trae 3 lentes hijos incluidos por defecto (LEN0026, LEN0027, LEN0028).")


def registrar_cli(app):
    app.cli.add_command(seed_recursos_cmd)
