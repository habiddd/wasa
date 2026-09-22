# No-show automático y avisos de recogida (22 de septiembre de 2026)

## Resumen

Una reserva `confirmada` que nadie recoge ya no se queda así para siempre:
`NO_SHOW_MARGEN_MIN` minutos después de `inicio` (default 30) pasa sola a
`no_show`, con tres avisos de WhatsApp al estudiante por el camino (`previo`,
`hora`, `no_show`). Si el estudiante aparece tarde pero su ventana todavía no
cerró, se le entrega igual y queda marcado como **entrega tardía** -- una
bandera, no un estado nuevo.

Piezas nuevas: `app/avisos_reservas.py` (el corazón: `barrer()`),
`app/ticker.py` (el hilo de fondo que lo dispara cada minuto), la tabla
`reserva_avisos`, la columna `reservas.entrega_tardia`, y los cambios de
`actas_service` que dejan firmar la ENTREGA de un grupo en `no_show`.

## Por qué un ticker + barrido perezoso, y no un cron

Se descartó sumar APScheduler, Celery o un contenedor nuevo: el volumen de
esta app (decenas de reservas, no miles) no lo justifica, y cada pieza nueva
es más superficie que mantener. En cambio:

- **El ticker** (`app/ticker.py`): un `threading.Thread(daemon=True)` que
  arranca en `create_app` y duerme `BARRIDO_INTERVALO_SEG` segundos (default
  60) entre corridas de `avisos_reservas.barrer()`. Duerme primero y barre
  después, para no pegarle a Postgres mientras el contenedor todavía está
  levantando.
- **El barrido perezoso**: `GET /dashboard/api/reservas` (el listado que el
  dashboard ya consulta cada 30 s) también llama a `barrer()` de paso, como
  segunda vía -- por ejemplo, justo después de levantar el contenedor y antes
  de que el ticker complete su primer sueño de 60 s.

Los dos llaman exactamente a la misma función. No hay dos implementaciones
del barrido, solo dos disparadores.

## Dos capas contra avisos duplicados, y por qué las dos hacen falta

`gunicorn` corre con `--workers 2` (ya estaba así por el timeout del LLM, ver
`flask-app/Dockerfile`), así que hay DOS procesos, cada uno con su propio
hilo ticker, más el barrido perezoso disparándose desde cualquier request al
listado. Sin protección, los dos podrían intentar mandar el mismo aviso.

1. **`avisos_reservas.intentar_lock`**: un lock de Redis (`SET NX EX`) que
   hace que, en el caso normal, un solo proceso corra el barrido por
   intervalo. Es la OPTIMIZACIÓN -- evita trabajo repetido, nada más.
2. **El `UNIQUE (grupo_id, tipo)` de `reserva_avisos`**: la garantía REAL. Si
   el lock vence a mitad de una corrida larga, o Redis está caído
   (`intentar_lock` devuelve `False` y loguea un warning, nunca lanza), dos
   workers pueden llegar a intentar el mismo aviso -- el segundo `INSERT`
   revienta con `IntegrityError`, ese worker hace `rollback()` y sigue con el
   resto del lote.

La razón de fondo para no confiar solo en el lock: Redis es infraestructura
que puede fallar, y un aviso duplicado (dos "tu reserva se liberó" al mismo
estudiante) es peor que un tick perdido. La base de datos, con una
restricción `UNIQUE`, no falla de la misma manera.

## El corte de frescura, y por qué es imprescindible

`AVISO_VENTANA_MIN` (default 120 minutos): un aviso solo se ENVÍA si su
momento ideal cayó dentro de los últimos `AVISO_VENTANA_MIN` minutos. Si el
contenedor estuvo caído un rato (mantenimiento, un despliegue, lo que sea) y
vuelve a levantar, el primer barrido se encuentra con reservas cuyo momento
de aviso ya pasó hace horas. Sin este corte, ese barrido mandaría "te
recuerdo que recoges en 30 min" con dos horas de retraso -- eso no es un
aviso tardío, es información incorrecta, y confunde más de lo que ayuda.

Si el momento ya venció, la fila de `reserva_avisos` se crea igual con
`enviado=false, motivo='vencido'`, para no reintentarlo nunca en el
siguiente tick (el `UNIQUE` ya lo bloquearía de todas formas, pero escribir
la fila de una vez evita que el barrido vuelva a evaluar ese mismo `momento >
ahora` en cada tick para siempre).

El cambio de estado a `no_show` SÍ se aplica aunque el aviso de ese tipo esté
vencido: el estado es un hecho (el activo se liberó, alguien más podría
necesitarlo), el mensaje es una cortesía con fecha de vencimiento. Separar
las dos cosas fue una decisión explícita del usuario.

## `entrega_tardia`: bandera, no estado

La alternativa obvia era un estado nuevo (`entregada_tarde`, por ejemplo). Se
descartó porque **no sobrevive a la devolución**: en cuanto se registrara el
acta de devolución, `estado` pasaría a `completada`/`devuelta_tarde` y el
hecho de que la entrega fue tardía se perdería para siempre. Con una columna
booleana aparte (`Reserva.entrega_tardia`), el dato queda para siempre,
independiente de por dónde siga viajando `estado`.

`actas_service.aplicar_transicion` ahora acepta como objetivo de la ENTREGA
tanto líneas `confirmada` como `no_show`; las que venían de `no_show` se
marcan `entrega_tardia=True`. Si `max(fin)` de las líneas objetivo ya pasó
(la ventana cerró para todas), no se registra nada -- error explícito en vez
de una entrega fantasma.

**El horario NO se extiende.** `fin` no se toca en ningún punto de este
flujo: una reserva de 4 horas recogida con 1 hora de retraso deja 3 horas de
uso real, no 4. Fue una decisión explícita, no un descuido.

## Verificación

Todo contra el stack real en Docker (nunca `vite`/`node` en el host), con
datos propios creados y borrados al terminar.

**1. Idempotencia del esquema.** `postgres/init/02-schema.sql` corrido dos
veces seguidas contra la base viva (`docker compose exec -T postgres psql -U
postgres -d reservas_db`, el superusuario -- `reservas_user` no es dueño de
las tablas). Primera corrida: `ALTER TABLE`, `CREATE INDEX` y `CREATE TABLE`
de las piezas nuevas. Segunda corrida: `NOTICE: ... already exists,
skipping` para las tres, sin errores. `\d reservas` y `\d reserva_avisos`
confirman la columna, el índice y la tabla con sus constraints
(`reserva_avisos_unico UNIQUE (grupo_id, tipo)`, `reserva_avisos_tipo_valido`,
FK `ON DELETE CASCADE`).

**2. El ticker arranca y corre solo.** `docker compose build flask-app &&
docker compose up -d flask-app`. Log al arrancar (en los dos workers):

```
[...] WARNING in ticker: Ticker de avisos_reservas iniciado (cada 60 s).
[...] WARNING in ticker: Ticker de avisos_reservas iniciado (cada 60 s).
```

(Nota aparte: las primeras versiones de `ticker.py` usaban
`app.logger.info(...)`, y esos mensajes salían mudos -- este proyecto no
configura `logging` propio, así que sin handler explícito Python solo saca
por stderr los niveles WARNING para arriba, el "lastResort" de la librería
estándar. Es el mismo motivo por el que el resto del código, `whatsapp_client`
o `sesiones.py`, tampoco usa `.info` en ningún lado. Se corrigió a
`.warning`.)

Se confirmaron ticks corriendo solos, sin invocar nada manualmente, cada ~60
s:

```
21:54:31 Barrido de avisos_reservas: {'grupos': 2, 'avisos_enviados': 0, 'avisos_vencidos': 0, 'no_show': 0}
21:55:31 Barrido de avisos_reservas: {'grupos': 1, 'avisos_enviados': 0, 'avisos_vencidos': 0, 'no_show': 0}
21:56:41 Barrido de avisos_reservas: {'grupos': 2, 'avisos_enviados': 0, 'avisos_vencidos': 0, 'no_show': 0}
```

Sin excepciones en ningún tick.

**3. Prueba del barrido con datos propios**, usuario de pruebas
`000000000000` (id=1, ya existente -- no se creó ni se borró, es la persona
fixture de las pruebas E2E de este proyecto), reservas propias creadas y
borradas con `docker compose exec flask-app python <script>` (vía
`app.avisos_reservas.barrer()` directo, sin pasar por HTTP):

- **Grupo "vencido"** (`inicio` 3 h atrás): las tres filas de
  `reserva_avisos` salieron `enviado=false, motivo='vencido'` -- nada se
  mandó -- y la línea pasó a `no_show` igual, confirmando que el cambio de
  estado no depende de la frescura del aviso.
- **Grupo "fresco"** (`inicio` en 25 min, aviso `previo` ya vencido pero
  dentro de la ventana de 120 min): se intentó mandar (no quedó
  `motivo='vencido'`); Baileys devolvió un timeout de lectura en ese intento
  puntual, así que quedó `enviado=false, motivo='error_envio'` -- el camino
  correcto para un envío que se intentó a tiempo pero falló, distinto del
  camino "vencido" que ni se intenta.
- **Segunda corrida**, inmediatamente después: `{'avisos_enviados': 0,
  'avisos_vencidos': 0, 'no_show': 0}` -- cero filas nuevas, confirmando la
  idempotencia (el grupo vencido ya ni siquiera vuelve a aparecer en la
  consulta, porque salió de `estado='confirmada'`).
- Aparte, con datos reales de `actas_service` (no del barrido): un grupo
  `no_show` con `fin` en el futuro sí pudo registrar la ENTREGA
  (`momento_siguiente` devolvió `'entrega'`, quedó `entregada` con
  `entrega_tardia=True`, `fin` intacto); uno con `fin` ya pasado devolvió el
  error esperado y no cambió nada.

Todos los datos de prueba (grupos, reservas, filas de `reserva_avisos`) se
borraron al terminar cada prueba.

**4. Las 27 `confirmada` viejas.** Confirmado: quedaron 27 filas en
`no_show`. La mayoría (24 de 27, por tipo de aviso) salieron con
`motivo='vencido'` sin mandar nada, como esperaba el plan.

**Aparte, algo que vale la pena que el usuario sepa** (no es una de "las 27"
dummy, es dato real): dos grupos del propio número de pruebas manual del
usuario (`573045246117`, "Habid abdala") tenían reservas `confirmada` reales
cuyo momento de aviso cayó DENTRO de la ventana de frescura justo mientras
se verificaba esta sesión:

- Grupo 77 (Sony A7III + 2 lentes, `inicio` 21:00 UTC / 16:00 local) ya tenía
  sus 30 minutos de margen vencidos al reconstruir el contenedor -- el primer
  tick le mandó los tres avisos (`previo`, `hora`, `no_show`) de verdad y la
  pasó a `no_show`.
- Grupo 64 (Canon EOS R + 2 lentes, `inicio` 22:24 UTC / 17:24 local, todavía
  futuro) recibió el aviso `previo` de verdad; sigue `confirmada` y le tocan
  `hora` a las 22:24 UTC y `no_show` a las **22:44 UTC** si nadie la recoge
  antes. Ojo con ese 22:44: el `fin` de esa reserva es 22:44 y el margen de
  no-show caería en 22:54, así que manda el `min(inicio + margen, fin)` --
  es justo el caso de reserva más corta que el margen para el que existe ese
  `min`.

Esto no es un bug ni un dato de prueba que haya que limpiar -- es la
funcionalidad nueva operando por primera vez contra reservas reales que ya
estaban vivas en la base al momento del despliegue. Se deja anotado para que
no sea una sorpresa si el usuario revisa su WhatsApp. En total salieron
**cuatro** mensajes reales, todos al número del propio usuario: ninguno a las
reservas dummy.

**Epílogo del grupo 77: el ciclo completo, probado en el navegador.** Minutos
después de que el barrido lo dejara en `no_show`, el usuario lo firmó a mano
desde el dashboard. Quedó `completada` con `entrega_tardia = true` y dos actas
reales en el volumen (`77/20260922T215746-entrega-firmada.pdf`, 17 687 bytes,
y `77/20260922T215908-devolucion-firmada.pdf`, 38 077 bytes). Eso cierra de
punta a punta el camino que la verificación automática no podía tocar:

```
confirmada → (barrido) no_show → (acta de ENTREGA dentro de la ventana)
           → entregada + entrega_tardia → (acta de DEVOLUCIÓN) → completada
```

Y confirma las dos propiedades que más importaban del diseño: que un grupo en
`no_show` sí acepta su acta de entrega mientras su horario siga vivo, y que la
bandera `entrega_tardia` **sobrevive a la devolución** -- que fue la razón de
hacerla bandera y no un noveno estado. Si hubiera sido un estado, el paso a
`completada` habría borrado el dato.

**5. Frontend.** `docker compose build dashboard-frontend`: `tsc -b && vite
build` sin errores (2197 módulos). `docker compose up -d dashboard-frontend`,
contenedor arriba. Se agregó `Sesion.entrega_tardia` en `lib/api.ts`,
`EventoCalendario.entregaTardia` en `calendario-utils.ts` y el badge
"Entrega tardía" (`variant="warning"`, reutilizando el mismo tono que
`no_show`/`pendiente`) en `EstadoBadge.tsx`, al lado del de "Retrasada" --
como `EstadoEventoBadge` ya es el único punto donde se pinta ese badge
(`ReservasPanel.tsx` y `ReservaDetalle.tsx` lo importan de ahí, no dibujan
uno propio), un solo cambio cubre los tres sitios que pedía el plan.

**6. Playwright.** No se usó, como pide la regla del proyecto. Toda la
verificación de arriba es por API/DB directa y por logs.

## Desviación del plan

- `ticker.py` usaba `app.logger.info(...)` según el plan original (que no
  especificaba el nivel); se cambió a `.warning()` porque el proyecto no
  configura `logging` y los `.info()` quedan mudos en `docker compose logs`
  -- ver el punto 2 de la verificación. Es el único cambio de fondo respecto
  a lo escrito en el plan; todo lo demás se implementó tal cual.

## Pendiente / fuera de alcance de esta sesión

- No se agregó un filtro "Entrega tardía" en `ReservasPanel.tsx`/
  `CalendarioPanel.tsx` (como existe hoy para "Retrasada"): el plan solo
  pedía el badge, no un filtro nuevo, así que no se inventó uno.
- ~~No se probó el flujo de firma de acta con entrega tardía en el
  navegador.~~ Quedó probado poco después, por el usuario y a mano, sobre el
  grupo 77 -- ver el epílogo al final de la sección de verificación. La
  lógica de backend (`actas_service`) ya estaba probada directamente
  (sección 3, último punto).
- Queda una carrera estrecha en `barrer()`: el WhatsApp se manda ANTES del
  `commit`, para poder guardar si Baileys lo aceptó. Si los dos workers
  pasaran el lock a la vez (Redis caído, o lock vencido a mitad de corrida),
  ambos mandarían el mensaje y solo uno insertaría la fila -- aviso
  duplicado para el estudiante, histórico y estado correctos igual. Se dejó
  así a conciencia; el razonamiento y la forma de cerrarla están en
  `docs/ARQUITECTURA.md`, "Qué garantiza exactamente el `UNIQUE`, y qué no".
- No hay un interruptor único para dejar de mandar avisos: `BARRIDO_ACTIVO=0`
  apaga el ticker pero no el barrido perezoso. Anotado como pendiente en
  `docs/SEGURIDAD.md`, junto con el rodeo que sí funciona
  (`AVISO_VENTANA_MIN=0`).
- Nada de esto está commiteado -- el usuario no lo pidió.
