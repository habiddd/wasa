"""
Robustez del bot tras la prueba E2E (docs/PLAN_robustez_bot_2026-09-30.md, F1 a
F8). Llama a `gestionar_reserva` / `gestionar_cancelacion` / las funciones
puras directo (no el webhook), con un usuario ficticio: no manda mensajes. Los
servicios hacen db.session.commit(): se parchea a flush y todo (usuario,
reservas) se revierte al final, la base queda como estaba.

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_robustez_bot.py
"""

import copy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app import create_app
from app import llm_client as L
from app import menu_service as M
from app import recursos_service as R
from app.extensions import db
from app.models import Recurso, Reserva, Usuario

TELEFONO = "573009990099"
CAMARA, TRIPODE, SALA = 3, 4, 9  # EOS R6, Trípode Manfrotto, Sala de edición 1

app = create_app()
with app.app_context():
    assert not Usuario.query.filter_by(telefono_wa=TELEFONO).count()
    db.session.commit = db.session.flush
    # Los casos usan activos concretos (micrófono, trípode...): se ponen todos
    # 'disponible' dentro de la transacción, que se revierte al final, para
    # que la prueba no dependa de lo que otras pruebas dejaron 'no_devuelto'.
    Recurso.query.update({"estado": "disponible"})
    u = Usuario(nombre="Persona Robustez", telefono_wa=TELEFONO, codigo_uninorte="R99999",
                cedula="99999999", registro_completo=True)
    db.session.add(u)
    db.session.flush()

    ahora_local = datetime.now(timezone.utc).astimezone(R.ZONA_HORARIA_LACS)
    hoy = ahora_local.date()

    def anio_de(mes, dia):
        return hoy.year if date(hoy.year, mes, dia) >= hoy else hoy.year + 1

    def dia_semana(i):  # próxima fecha (hoy incluido) con ese día de la semana
        return hoy + timedelta(days=(i - hoy.weekday()) % 7)

    def paso(funcion, texto, sesion):
        respuesta, sesion = funcion(texto, u, sesion)
        print(f"  {texto!r}\n    -> {respuesta[-110:]!r}")
        return respuesta, sesion

    def reservar(texto, sesion):
        return paso(R.gestionar_reserva, texto, sesion)

    def cancelar(texto, sesion):
        return paso(R.gestionar_cancelacion, texto, sesion)

    def numero(sesion, fragmento):
        """Número del menú actual cuya etiqueta trae `fragmento`."""
        opciones = sesion["reserva_wizard"]["opciones_actuales"]
        return str(next(i for i, (_, e) in enumerate(opciones, 1) if fragmento in e.lower()))

    def reservar_en(recurso_id, dia, hora_ini=22, hora_fin=23):
        ini = datetime(dia.year, dia.month, dia.day, hora_ini, tzinfo=R.ZONA_HORARIA_LACS)
        fin = datetime(dia.year, dia.month, dia.day, hora_fin, tzinfo=R.ZONA_HORARIA_LACS)
        reservas, error = R.crear_reservas_grupo(
            u.id, [db.session.get(Recurso, recurso_id)], ini.astimezone(timezone.utc), fin.astimezone(timezone.utc)
        )
        assert error is None, error
        return reservas[0]

    def dia_habil(d):
        while R._error_fecha_bloqueada({}, d):
            d += timedelta(days=7)
        return d

    def hasta_hora_inicio(texto, dia):
        r, s = reservar(texto, {})
        r, s = reservar("1", s)
        r, s = reservar("1", s)
        for _ in range(3):
            p = s["reserva_wizard"]["paso"]
            if p in ("confirmar_contenidos_espacio", "confirmar_accesorios"):
                r, s = reservar("no", s)
            elif p == "sale_de_barranquilla":
                r, s = reservar("2", s)
        r, s = reservar(dia.strftime("%d/%m/%Y"), s)
        assert "HORA DE INICIO" in r, r
        return s

    def estado(reserva):
        db.session.expire_all()
        return db.session.get(Reserva, reserva.id).estado

    # --- F1. "sí, cancelar" cancela; "no" y "cerrar" no
    print("F1")
    r1 = reservar_en(TRIPODE, dia_semana(2) + timedelta(days=21))
    for texto in ("no", "cerrar", "no gracias"):
        r, s = cancelar("cancelar", {})
        assert "TUS RESERVAS ACTIVAS" in r
        r, s = cancelar("1", s)
        assert "CONFIRMAR CANCELACIÓN" in r
        r, s = cancelar(texto, s)
        assert "No cancelé" in r and s["cancelacion_wizard"] is None and estado(r1) != "cancelada"
    for texto in ("si, cancelar", "sí", "dale"):
        r, s = cancelar("cancelar", {})
        r, s = cancelar("1", s)
        r, s = cancelar(texto, s)
        assert "Listo, cancelé esa reserva" in r and estado(r1) == "cancelada", texto
        r1.estado = "confirmada"  # para probar el siguiente sinónimo
        db.session.flush()
    r1.estado = "cancelada"
    db.session.flush()

    # --- F2. Horas en palabras
    print("F2")
    hora = lambda t: R._parsear_hora_individual(t) or R._interpretar_hora_individual_relajada(t)  # noqa: E731
    for texto, esperado in {
        "9 de la mañana": (9, 0), "9 de la manana": (9, 0), "9 en la mañana": (9, 0),
        "2 de la tarde": (14, 0), "7 de la noche": (19, 0), "mediodía": (12, 0), "medio dia": (12, 0),
        "12 del mediodía": (12, 0), "9 y media": (9, 30), "9 y cuarto": (9, 15),
        "2 y media de la tarde": (14, 30), "3": (15, 0), "10": (10, 0), "3pm": (15, 0), "a las 3 pm": (15, 0),
        "15:00": (15, 0), "las 15 horas": (15, 0), "99": None, "hola": None,
    }.items():
        assert hora(texto) == esperado, (texto, hora(texto))

    # --- F3. Fechas y rangos en palabras
    print("F3")
    a = anio_de(10, 9)
    rango = lambda t: R._interpretar_rango_fecha_natural(t, ahora_local)  # noqa: E731
    fecha = lambda t: R._interpretar_fecha_natural(t, ahora_local)  # noqa: E731
    esperado = (date(a, 10, 7), date(a, 10, 9))
    for texto in ("del 7 al 9 de octubre", "entre el 7 y el 9 de octubre", "del 7 de octubre al 9 de octubre",
                  "desde el 7 hasta el 9 de octubre"):
        assert rango(texto) == esperado, (texto, rango(texto))
    assert rango("de mañana a pasado mañana") == (hoy + timedelta(days=1), hoy + timedelta(days=2))
    assert rango("de 9 a 11") is None
    a = anio_de(10, 12)
    assert fecha("lunes 12 de octubre") == fecha("el lunes 12 de octubre") == date(a, 10, 12)
    assert fecha("el 5 de octubre") == fecha("para el 5 de octubre") == date(anio_de(10, 5), 10, 5)
    assert fecha("el lunes") == dia_semana(0) and fecha("mañana") == hoy + timedelta(days=1)
    assert fecha("5 de octubre de 2031") == date(2031, 10, 5)

    # --- F4. Lo que el primer mensaje ya decía
    print("F4")
    d = R._extraer_detalles_de_texto("necesito el tripode manfrotto para mañana de 9 a 11 de la mañana", ahora_local)
    assert fecha(d["fecha"]) == hoy + timedelta(days=1) and hora(d["hora_inicio"]) == (9, 0) and hora(d["hora_fin"]) == (11, 0), d
    d = R._extraer_detalles_de_texto(
        "me gustaria apartar la sala de edicion el lunes 5 de octubre de 2 a 5 de la tarde", ahora_local)
    assert fecha(d["fecha"]) == date(anio_de(10, 5), 10, 5) and hora(d["hora_inicio"]) == (14, 0) and hora(d["hora_fin"]) == (17, 0), d
    d = R._extraer_detalles_de_texto("quiero el microfono rode y el tripode para el martes 6 de octubre", ahora_local)
    assert fecha(d["fecha"]) == date(anio_de(10, 6), 10, 6) and "hora_inicio" not in d, d
    d = R._extraer_detalles_de_texto("del 7 al 9 de octubre de 9 a 11 de la mañana", ahora_local)
    assert rango(d["fecha"]) == (date(anio_de(10, 7), 10, 7), date(anio_de(10, 9), 10, 9)) and hora(d["hora_inicio"]) == (9, 0), d
    assert R._extraer_detalles_de_texto("de 5 a 7 de octubre", ahora_local).keys() == {"fecha"}
    assert R._extraer_detalles_de_texto("quiero reservar una cámara", ahora_local) == {}
    assert R._extraer_detalles_de_texto("por la mañana", ahora_local) == {}  # "de la mañana" no es la fecha "mañana"
    d = R._extraer_detalles_de_texto("el trípode el 07/10/2026 a 09/10/2026 a las 3pm", ahora_local)
    assert d["fecha"] == "07/10/2026 a 09/10/2026" and hora(d["hora_inicio"]) == (15, 0), d

    # gestionar_reserva de la primera frase: llega a "Entendí ..." sin pedir la fecha en blanco
    r, s = reservar("necesito el tripode manfrotto para mañana de 9 a 11 de la mañana", {})
    assert "particular o para una asignatura" in r
    r, s = reservar("1", s)
    r, s = reservar("1", s)  # individual
    assert "SALIDA DE BARRANQUILLA" in r
    r, s = reservar("2", s)
    manana = hoy + timedelta(days=1)
    error_manana = R._error_fecha_bloqueada({}, manana)
    if error_manana:  # mañana cae en sábado/domingo/festivo: el error y la pregunta normal de la fecha
        assert error_manana in r and "¿Qué fecha necesitas?" in r
        print("  (mañana no es hábil: se probó el camino de error del prefill)")
    else:
        assert f"Entendí {R._fecha_legible(manana)}" in r and "FECHA DE RESERVA" not in r
        r, s = reservar("1", s)
        assert "Entendí 9:00 a.m." in r and "HORA DE INICIO" not in r
        r, s = reservar("1", s)
        assert "Entendí 11:00 a.m." in r and "HORA DE FIN" not in r
        r, s = reservar("1", s)
        if "NOTA DE LA RESERVA" in r:
            r, s = reservar("2", s)
            assert "RESUMEN DE RESERVA" in r and "Trípode Manfrotto" in r
            assert "9:00 a.m." in r and "11:00 a.m." in r
        else:  # el trípode ya está reservado mañana en ese horario: llegó hasta la disponibilidad
            assert "No está disponible en ese horario" in r and "Trípode Manfrotto" in r, r

    # varios activos: el resumen trae micrófono y trípode
    r, s = reservar("quiero el microfono rode y el tripode para el martes 6 de octubre", {})
    assert len(s["reserva_wizard"]["por_agregar"]) == 1
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    assert "Micrófono Rode NTG3" in r and "Trípode Manfrotto" in r and "SALIDA DE BARRANQUILLA" in r, r
    r, s = reservar("2", s)
    martes = date(anio_de(10, 6), 10, 6)
    if R._error_fecha_bloqueada({}, martes):
        assert "¿Qué fecha necesitas?" in r
    else:
        assert f"Entendí {R._fecha_legible(martes)}" in r, r
        r, s = reservar("1", s)
        assert "HORA DE INICIO" in r  # sin hora en el mensaje: se pregunta normal
        r, s = reservar("4pm", s)
        r, s = reservar("5pm", s)
        r, s = reservar("2", s)
        assert "RESUMEN DE RESERVA" in r and "Micrófono Rode NTG3" in r and "Trípode Manfrotto" in r

    # accesorios excluidos
    r, s = reservar("la canon eos r6 pero sin el lente de 100-500", {})
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    carrito = [i["nombre_recurso"] for i in s["reserva_wizard"]["carrito"]]
    assert "Cámara Canon EOS R6" in carrito and not any("100-500" in n for n in carrito), carrito
    assert any("16-35" in n for n in carrito) and "Quité Lente Canon RF 100-500mm f/4.5-7.1L como pediste." in r, r
    r, s = reservar("la canon eos r6 pero sin lentes", {})
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    carrito = [i["nombre_recurso"] for i in s["reserva_wizard"]["carrito"]]
    assert carrito == ["Cámara Canon EOS R6"] and "Quité" in r and "SALIDA DE BARRANQUILLA" in r, (carrito, r)
    r, s = reservar("la canon eos r6", {})  # sin "sin": todos los accesorios quedan y se ofrece quitar
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    assert "100-500" in r and "Quité" not in r

    # --- F5. Consulta con fecha, y el "sí" que sigue
    print("F5")
    sabado = dia_semana(5)
    consulta = "me prestan las luces led este sábado?"
    r = R.evaluar_consulta_disponibilidad(consulta)
    print(f"  consulta {consulta!r}\n    -> {r!r}")
    assert "Kit de luces LED" in r and "no se pueden hacer reservas" in r.lower() and "sábado" in r, r
    assert "ese día" not in r  # día no hábil: no se contradice el aviso con "libre ese día" (F12)
    r = R.evaluar_consulta_disponibilidad("tienen luces led el lunes 5 de octubre?")
    assert "no se pueden hacer reservas" not in r.lower() and "ese día" in r, r
    r = R.evaluar_consulta_disponibilidad("tienen luces led?")  # sin fecha: el estado de ahora, como siempre
    assert "ese día" not in r and "Kit de luces LED" in r
    respuesta, sesion = M.activar_oferta({}, r, consulta)
    accion, payload, sesion, _ = M.procesar_respuesta_oferta("sí", sesion)
    assert accion == "reservar" and payload == consulta and sesion[M.CLAVE_OFERTA] is None
    _, sesion_vieja = M.activar_oferta({}, r)  # sin texto de consulta: el payload sigue siendo el "sí"
    assert M.procesar_respuesta_oferta("sí", sesion_vieja)[1] == "sí"
    r, s = reservar(payload, {})
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    assert "Kit de luces LED" in r and "SALIDA DE BARRANQUILLA" in r
    r, s = reservar("2", s)
    if R._error_fecha_bloqueada({}, sabado):
        assert "No se pueden hacer reservas para el sábado" in r and "¿Qué fecha necesitas?" in r, r

    # --- F6. Cancelar por descripción
    print("F6")
    lunes, martes_c, miercoles = (dia_semana(i) for i in (0, 1, 2))
    rs = reservar_en(SALA, lunes), reservar_en(CAMARA, martes_c), reservar_en(TRIPODE, miercoles)
    r, s = cancelar("cancela la sala de edición del lunes", {})
    assert "CONFIRMAR CANCELACIÓN" in r and "Sala de edición 1" in r and "Trípode" not in r, r
    assert s["cancelacion_wizard"]["paso"] == "confirmar_cancelacion" and s["cancelacion_wizard"]["cantidad_reservas"] == 1
    r, s = cancelar("no", s)
    assert "No cancelé" in r and all(estado(x) != "cancelada" for x in rs)
    r, s = cancelar("cancela el trípode", {})
    assert "CONFIRMAR CANCELACIÓN" in r and "Trípode Manfrotto" in r and "Sala" not in r
    r, s = cancelar("cancela lo del martes", {})
    assert "CONFIRMAR CANCELACIÓN" in r and "Canon EOS R6" in r and "Sala" not in r
    r, s = cancelar("cancelar mi reserva", {})  # sin pistas: la lista completa
    assert "TUS RESERVAS ACTIVAS" in r and "Sala de edición 1" in r and "Trípode" in r and "EOS R6" in r
    r, s = cancelar("cancela la sala de edición", {})  # un nombre que coincide
    assert "CONFIRMAR CANCELACIÓN" in r and "Trípode" not in r
    r, s = cancelar("cancela el micrófono", {})  # ninguna coincide: lista completa
    assert "TUS RESERVAS ACTIVAS" in r
    # F11: nombre y fecha no coinciden a la vez -> vale solo el nombre
    r, s = cancelar("cancela la sala de edición del martes", {})
    assert "CONFIRMAR CANCELACIÓN" in r and "Sala de edición 1" in r and "Trípode" not in r, r

    # F10: "jueves 15" (día de la semana + número sin mes) gana el número
    jueves = dia_semana(3) + timedelta(days=7)
    rj = reservar_en(SALA, jueves)
    n = jueves.day
    assert R._fechas_en_texto(f"del jueves {n}", ahora_local) == (jueves, jueves)
    assert R._fechas_en_texto(f"el {n}", ahora_local) == (jueves, jueves)
    assert R._fechas_en_texto(f"del martes {n}", ahora_local) == (jueves, jueves)  # no cuadra: gana el número
    assert R._extraer_detalles_de_texto("necesito 2 camaras a las 3 pm") == {"hora_inicio": "3 pm"}
    assert R._extraer_detalles_de_texto("el tripode el 5 de 9 a 11 de la mañana")["fecha"] == "el 5"
    assert "fecha" not in R._extraer_detalles_de_texto("el tripode del martes a las 3".replace("del martes ", ""))
    for texto in (f"cancela la sala de edición del jueves {n}", f"cancela la sala de edición del martes {n}"):
        r, s = cancelar(texto, {})
        assert "CONFIRMAR CANCELACIÓN" in r and f"{jueves.strftime('%d/%m/%Y')}" in r, r
    rj.estado = "cancelada"
    db.session.flush()

    # --- F7. Los espacios no "salen de Barranquilla"
    print("F7")
    r, s = reservar("quiero reservar la sala de edición", {})
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    if s["reserva_wizard"]["paso"] == "confirmar_contenidos_espacio":
        r, s = reservar("no", s)
    wizard = s["reserva_wizard"]
    assert "SALIDA DE BARRANQUILLA" not in r and "FECHA DE RESERVA" in r, r
    assert wizard["sale_de_barranquilla"] is False and wizard["sale_de_barranquilla_auto"] is True
    fecha_sala = dia_semana(3) + timedelta(days=14)
    r, s = reservar(fecha_sala.strftime("%d/%m/%Y"), s)
    r, s = reservar("4pm", s)
    r, s = reservar("5pm", s)
    r, s = reservar("2", s)
    assert "RESUMEN DE RESERVA" in r and "Sale de Barranquilla" not in r and "Cambiar si sale" not in r, r
    # si luego se agrega un equipo, el valor automático se descarta y se pregunta
    r, s = reservar(numero(s, "agregar otro"), s)
    r, s = reservar(numero(s, "cámara"), s)
    r, s = reservar(numero(s, "canon"), s)
    r, s = reservar(numero(s, "eos r6"), s)
    if s["reserva_wizard"]["paso"] == "confirmar_accesorios":
        r, s = reservar("no", s)
    assert "SALIDA DE BARRANQUILLA" in r and s["reserva_wizard"]["sale_de_barranquilla"] is None, r
    # sala + cámara desde el primer mensaje: sí se pregunta
    r, s = reservar("quiero la sala de edición y la canon eos r6", {})
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    for _ in range(2):  # contenidos del lugar y/o accesorios de la cámara, si los hay
        if s["reserva_wizard"]["paso"] in ("confirmar_contenidos_espacio", "confirmar_accesorios"):
            r, s = reservar("no", s)
    assert "SALIDA DE BARRANQUILLA" in r and "Sala de edición 1" in r and "Cámara Canon EOS R6" in r, r

    # --- F8. Recordatorio con los minutos reales
    print("F8")
    def aviso(minutos):
        inicio = datetime.now(timezone.utc) + timedelta(minutes=minutos)
        reserva = SimpleNamespace(inicio=inicio, grupo_id=7, recurso=SimpleNamespace(nombre="Trípode Manfrotto"))
        return R.mensaje_aviso_previo([reserva])
    assert "en 25 minutos empieza" in aviso(25), aviso(25)
    assert "en 30 minutos empieza" in aviso(30) and "en 30 minutos empieza" in aviso(90)
    assert "en 1 minuto empieza" in aviso(0.2) and "en 1 minuto empieza" in aviso(-3)

    # --- F9. "si" no es una opción por estar dentro de "asignatura"; "micro" sí encuentra Micrófono
    print("F9")
    r, s = reservar("quiero reservar", {})
    assert "particular o para una asignatura" in r
    r, s = reservar("si", s)
    assert "No reconocí esa opción" in r and s["reserva_wizard"]["tipo_uso"] is None
    assert R._interpretar_opcion("si", R.OPCIONES_TIPO_USO) is None and R._interpretar_opcion("asignatura", R.OPCIONES_TIPO_USO) == "asignatura"
    r, s = reservar("1", s)
    r, s = reservar("1", s)
    assert "¿Qué necesitas reservar?" in r
    r, s = reservar("micro", s)
    carrito = [i["nombre_recurso"] for i in s["reserva_wizard"]["carrito"]]
    assert carrito == ["Micrófono Rode NTG3"], carrito

    # --- F12. Consulta de un día no hábil: el aviso y el estado de ahora, sin "libre ese día"
    print("F12")
    r = R.evaluar_consulta_disponibilidad("hay disponibilidad del dron el sabado?")
    assert "No se pueden hacer reservas para el sábado" in r and "Dron DJI Mavic" in r and "ese día" not in r, r

    # --- F13. "¿Qué reservas tengo?" lista, no abre el wizard
    print("F13")
    for texto in ("que reservas tengo?", "mis reservas", "tengo reservas?", "ver mis reservas", "que tengo reservado"):
        r, s = reservar(texto, {})
        assert "TUS RESERVAS ACTIVAS" in r and "Sala de edición 1" in r, (texto, r)
        assert not s.get("reserva_wizard") and not s.get("cancelacion_wizard"), texto
    assert R._PATRON_VER_RESERVAS.search("tengo que reservar") is None
    r, s = reservar("quiero reservar", {})
    r, s = reservar("que reservas tengo", s)
    assert "TUS RESERVAS ACTIVAS" in r and s["reserva_wizard"]["paso"] == "tipo_uso", r
    r, s = reservar("quiero cancelar la de la sala de edicion del martes", s)
    assert s["reserva_wizard"] is None and "anular una reserva que ya tienes" in r, r
    r, s = reservar("quiero reservar", {})
    r, s = reservar("no", s)
    assert s["reserva_wizard"] is None and "anular una reserva" not in r, r
    r, s = cancelar("quiero cancelar la de la sala de edicion del martes", {})
    assert "CONFIRMAR CANCELACIÓN" in r, r

    # --- F14. _interpretar_opcion: sí/no y raíces de palabras
    print("F14")
    op = R._interpretar_opcion
    for texto in ("si correcto", "si esta bien", "dale", "sí"):
        assert op(texto, R.OPCIONES_CONFIRMAR_FECHA) == "si", texto
    assert op("no gracias", R.OPCIONES_CONFIRMAR_HORA) == "no"
    assert op("no, el viernes", R.OPCIONES_CONFIRMAR_FECHA) is None
    assert op("si, pero a las 4", R.OPCIONES_CONFIRMAR_HORA) is None
    assert op("es para una asignatura", R.OPCIONES_TIPO_USO) == "asignatura"
    assert op("si", R.OPCIONES_TIPO_USO) is None
    for texto, esperado in {
        "listo, confirmala": "confirmar", "agregale tambien el microfono rode": "agregar_item",
        "cambiale la hora": "cambiar_hora", "cambia la fecha": "cambiar_fecha",
        "quitale el tripode": "quitar_item", "cambiar": None,
    }.items():
        assert op(texto, R.OPCIONES_CONFIRMAR) == esperado, (texto, op(texto, R.OPCIONES_CONFIRMAR))
    assert op("quiero grabar", [("x", "Estudio de grabación")]) is None

    # --- F15. Rango en el paso hora_inicio
    print("F15")
    D = dia_habil(dia_semana(2) + timedelta(days=35))
    s = hasta_hora_inicio("quiero reservar la sala de edición", D)
    r, s = reservar("de 10 y media a 12 del mediodia", s)
    assert "Entendí 10:30 a.m." in r, r
    r, s = reservar("1", s)
    assert "NOTA DE LA RESERVA" in r, r
    w = s["reserva_wizard"]
    ini, fin = (datetime.fromisoformat(w[k]).astimezone(R.ZONA_HORARIA_LACS) for k in ("inicio", "fin"))
    assert (ini.hour, ini.minute, fin.hour, fin.minute) == (10, 30, 12, 0), (ini, fin)

    # --- F16. Nota en el mismo "sí"
    print("F16")
    r, s2 = reservar("si por favor", copy.deepcopy(s))
    assert s2["reserva_wizard"]["paso"] == "nota_texto", r
    r, s2 = reservar("si, es para grabar una entrevista con el decano", copy.deepcopy(s))
    assert "RESUMEN DE RESERVA" in r and s2["reserva_wizard"]["nota"] == "es para grabar una entrevista con el decano", r
    r, s2 = reservar("agregale tambien el microfono rode", s2)
    assert "Micrófono Rode NTG3" in [i["nombre_recurso"] for i in s2["reserva_wizard"]["carrito"]]
    assert "¿Qué necesitas reservar?" not in r, r

    # --- F17. Menú de conflicto: cambiar fecha / hora
    print("F17")
    D2 = dia_habil(dia_semana(3) + timedelta(days=35))
    reservar_en(TRIPODE, D2, 10, 12)
    s = hasta_hora_inicio("necesito el tripode manfrotto", D2)
    r, s = reservar("10:00am", s)
    r, s = reservar("12:00pm", s)
    assert "No está disponible en ese horario" in r and "Cambiar fecha" in r and "Cambiar hora" in r, r
    r, s3 = reservar(numero(s, "cambiar fecha"), copy.deepcopy(s))
    assert "FECHA DE RESERVA" in r, r
    r, s3 = reservar(numero(s, "cambiar hora"), copy.deepcopy(s))
    assert "HORA DE INICIO" in r and s3["reserva_wizard"]["carrito"], r
    r, s3 = reservar("2pm", s3)
    r, s3 = reservar("4pm", s3)
    assert "NOTA DE LA RESERVA" in r, r

    # --- F18. Cancelar: ordinales y franja del día; estado de envío sin retroceso
    print("F18")
    assert R._interpretar_seleccion_multiple("la segunda", 2) == [1]
    assert R._interpretar_seleccion_multiple("primera y tercera", 3) == [0, 2]
    D3 = dia_habil(dia_semana(0) + timedelta(days=42))
    am, pm = reservar_en(TRIPODE, D3, 9, 10), reservar_en(TRIPODE, D3, 15, 16)
    f3 = D3.strftime("%d/%m/%Y")
    r, s = cancelar(f"cancela el tripode del {f3} en la tarde", {})
    assert "CONFIRMAR CANCELACIÓN" in r and "3:00 p.m." in r and "9:00 a.m." not in r, r
    r, s = cancelar(f"cancela el tripode del {f3} en la mañana", {})
    assert "CONFIRMAR CANCELACIÓN" in r and "9:00 a.m." in r and "3:00 p.m." not in r, r
    r, s = cancelar(f"cancela el tripode del {f3}", {})
    assert "TUS RESERVAS ACTIVAS" in r, r
    r, s = cancelar("la segunda", s)
    assert "CONFIRMAR CANCELACIÓN" in r and "3:00 p.m." in r and "9:00 a.m." not in r, r

    from app import guardrails as G
    from app.models import AuditoriaBot
    G.registrar_interaccion(TELEFONO, "x", None, "y", True, mensaje_id="wamid.F18", estado_envio="pendiente")
    for e in ("delivery_ack", "server_ack"):  # Meta los manda desordenados
        G.actualizar_estado_envio("wamid.F18", e)
    fila = AuditoriaBot.query.filter_by(mensaje_id="wamid.F18").one()
    assert fila.estado_envio == "delivery_ack", fila.estado_envio
    G.actualizar_estado_envio("wamid.F18", "leido")
    assert fila.estado_envio == "leido"

    db.session.rollback()
    assert not Usuario.query.filter_by(telefono_wa=TELEFONO).count()

print("OK: F1 a F18 (cancelar con 'sí, cancelar', horas y fechas en palabras, lo dicho en el primer mensaje, "
      "consulta con fecha + oferta, cancelar por descripción, espacios sin Barranquilla, recordatorio con minutos reales, "
      "ver mis reservas, sí/no y raíces, rango de horas, nota en el mismo sí, cambiar fecha/hora en conflicto, ordinales y franja al cancelar, estado de envío sin retroceso).")
