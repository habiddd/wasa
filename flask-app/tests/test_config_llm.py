"""Check de la config del LLM editable desde el dashboard. Usa una clave de
Redis de prueba (la borra al final) y requests.post parcheado: no toca la
config real ni llama al LLM. Correr:
docker exec -w /app -e PYTHONPATH=/app lacs_flask_app python tests/test_config_llm.py"""
from app import create_app, llm_client as L
from app.extensions import redis_client

L._CLAVE_CONFIG = "llm:config:prueba"


def test_config():
    app = create_app()
    post_real = L.requests.post
    with app.app_context():
        redis_client.delete(L._CLAVE_CONFIG)
        try:
            # sin nada guardado: .env, modo local
            c = L.config_llm()
            assert c["origen"] == ".env" and c["modo"] == "local"
            assert c["base_url"] == app.config["LLM_BASE_URL"].rstrip("/") and c["modelo"] == app.config["LLM_MODEL"]
            # validación: no guarda nada
            for malo in (("ftp://x", "m", "", "local"), ("http://", "m", "", "local"),
                         ("http://x", "", "", "local"), ("http://x", "m", "", "nube")):
                try:
                    L.guardar_config(*malo)
                    raise AssertionError(malo)
                except ValueError:
                    pass
            assert not redis_client.exists(L._CLAVE_CONFIG)
            # misma URL + key vacía = conserva la key
            L.guardar_config(c["base_url"], "otro/modelo", "", "online")
            c2 = L.config_llm()
            assert c2["modelo"] == "otro/modelo" and c2["modo"] == "online" and c2["api_key"] == c["api_key"]
            # otra URL + key vacía = sin key
            L.guardar_config("http://host.docker.internal:1234/", "qwen/qwen3-4b-2507", "", "local")
            c3 = L.config_llm()
            assert c3["base_url"] == "http://host.docker.internal:1234" and c3["api_key"] is None
            # key nueva; la vista pública no la expone
            L.guardar_config(c3["base_url"], c3["modelo"], "sk-prueba-abcd1234", "local")
            pub = L.config_publica()
            assert "sk-prueba-abcd1234" not in str(pub) and pub["api_key_final"] == "1234" and pub["origen"] == "dashboard"

            # muestreo según modo (requests.post falso: cae al heurístico)
            enviados = []

            def post_falso(url, json=None, headers=None, timeout=None):
                enviados.append((url, json, headers))
                raise L.requests.RequestException("sin red en el test")

            L.requests.post = post_falso
            L.clasificar_intent("necesito una camara para el viernes")
            url, payload, headers = enviados[-1]
            assert url == "http://host.docker.internal:1234/v1/chat/completions"
            assert payload["model"] == "qwen/qwen3-4b-2507" and payload["top_k"] == 20 and payload["temperature"] == 0.6
            assert headers == {"Authorization": "Bearer sk-prueba-abcd1234"}
            L.guardar_config(c3["base_url"], c3["modelo"], "", "online")
            L.clasificar_intent("necesito una camara para el viernes")
            payload = enviados[-1][1]
            assert "top_k" not in payload and "top_p" not in payload and payload["temperature"] == 0.2

            # borrar = vuelve a .env
            L.borrar_config()
            assert L.config_llm() == c
        finally:
            L.requests.post = post_real
            redis_client.delete(L._CLAVE_CONFIG)


if __name__ == "__main__":
    test_config()
    print("config_llm OK")
