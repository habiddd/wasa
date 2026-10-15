"""Check del menú de fallback (módulo puro: sin DB, sin LLM).
Correr dentro del contenedor: python tests/test_menu_service.py"""
from app import menu_service as M

MENU = {M.CLAVE_SESION: {"paso": "elegir_opcion", "intentos": 0}}
CONSULTA = {M.CLAVE_SESION: {"paso": "esperando_consulta", "intentos": 0}}


def accion(texto, sesion=MENU):
    return M.procesar_respuesta(texto, sesion)[0]


def test_menu():
    # opciones inequívocas: número, nombre a secas, salida
    assert accion("1") == "reservar"
    assert accion("2") == "responder"  # pregunta sobre qué equipo
    assert accion("3") == "cancelar"
    assert accion("Cancelar") == "cancelar"
    assert accion("reservar!") == "reservar"
    assert accion("cerrar") == "responder"
    assert accion("7") == "responder"  # número fuera de rango -> reintento
    # texto libre -> al clasificador, no a una opción por subcadena
    assert accion("mi reserva quedo confirmada?") == "seguir_curso"
    assert accion("listo, muchas gracias. chao") == "seguir_curso"
    assert accion("quiero reservar la sala") == "seguir_curso"
    # seguir_curso devuelve la sesión sin el menú
    assert M.procesar_respuesta("hola", MENU)[2][M.CLAVE_SESION] is None
    # en "¿sobre qué equipo?" el texto es el nombre del equipo, salvo cancelar
    assert accion("camara sony", CONSULTA) == "consultar"
    assert accion("mejor cancela la del viernes", CONSULTA) == "cancelar"


if __name__ == "__main__":
    test_menu()
    print("menu_service OK")
