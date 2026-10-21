# Recursos y espacios de Delta

Recursos y espacios que hoy se prestan por Delta, transcritos tal cual aparecen allí (2026-10-20). Son los datos reales que se pudieron obtener de la plataforma; Delta no muestra las placas de inventario.

## Espacios

| Código Delta | Nombre |
|---|---|
| `P BIBLI EA01 01` | ESTUDIO DE AUDIO |
| `P BIBLI ET01 NO` | ESTUDIO DE TELEVISION |
| `P BIBLI SE04 NO` | SALA DE EDICION |

## Activos

Nombres tal cual aparecen en Delta (con sus errores de escritura). En Delta hay al menos uno de cada uno; por ahora se registra una unidad por activo. "CAMARA DE VIDEO SONY Z150" aparece 3 veces en Delta e "IK MULTIMEDIA IRIG MIC DURO" 2 veces, pero aquí van una sola vez.

| # | Nombre en Delta | Cantidad |
|---|---|---|
| 1 | ADAPTADOR LIGHTNING a 3.5mm | 1 |
| 2 | ADAPTADOR TIPO C a 3.5mm | 1 |
| 3 | AUDIFONOS AKG K-240 STUDIO | 1 |
| 4 | CAMARA CANON T5i | 1 |
| 5 | CAMARA CANON T6i | 1 |
| 6 | CAMARA CANON T7i | 1 |
| 7 | CAMARA CANON T8i | 1 |
| 8 | CAMARA DE VIDEO GO PRO HERO 4 | 1 |
| 9 | CAMARA DE VIDEO SONY Z150 | 1 |
| 10 | CAMARA INSTA 360 ONE X2 | 1 |
| 11 | CAMARA SONY A7III | 1 |
| 12 | ESTABILIZADOR OSMO MOBILE 2 | 1 |
| 13 | ESTABILIZADOR OSMO MOBILE 4 | 1 |
| 14 | ESTABILIZADOR OSMO MOBILE 6 | 1 |
| 15 | FLEX | 1 |
| 16 | FRESNEL APUTURE F10 | 1 |
| 17 | GRABADOR EDIROL R4 | 1 |
| 18 | GRABADORA ZOOM P4 PODCAST | 1 |
| 19 | GRABADORAS TASCAM DR40 | 1 |
| 20 | GRABADORAS TASCAM DR40X | 1 |
| 21 | IK MULTIMEDIA IKLIP A/V | 1 |
| 22 | IK MULTIMEDIA IRIG MIC DURO | 1 |
| 23 | IK MULTIMEDIA IRIG MIC LAV | 1 |
| 24 | IK MULTIMEDIA IRIG PRE | 1 |
| 25 | KIT DE LUCES APUTURE 300 | 1 |
| 26 | KIT DE LUCES GVM | 1 |
| 27 | KIT DE LUCES INTELLYTECH 120W | 1 |
| 28 | KIT DE LUCES INTELLYTECH 160W | 1 |
| 29 | KIT DE LUCES LUME CUBE | 1 |
| 30 | KIT LENTE BEASTGRIP | 1 |
| 31 | KIT LENTES CELULARES MACTREM | 1 |
| 32 | KIT MIC HOLLYLAND LARK M2 | 1 |
| 33 | KIT MICRO BOOM SENNHEISER ME67 | 1 |
| 34 | KIT MICROFO SOLAPA SENNHEISER | 1 |
| 35 | KIT MICROFONO AVX | 1 |
| 36 | LENTE CANON Efs 10 - 18 mm | 1 |
| 37 | LENTE CANON 17-40mm | 1 |
| 38 | LENTE CANON 18-55mm | 1 |
| 39 | LENTE CANON 50MM | 1 |
| 40 | LENTE CANON 55-250mm | 1 |
| 41 | LENTE CANON 75-300mm | 1 |
| 42 | LENTE ROKINON 16MM | 1 |
| 43 | LENTE ROKINON 24 MM | 1 |
| 44 | LENTE ROKINON 44 MM | 1 |
| 45 | LENTE ROKINON 50mm | 1 |
| 46 | LENTE ROKINON 85MM | 1 |
| 47 | LUCES ARRI 1000 | 1 |
| 48 | LUCES ARRI 300 | 1 |
| 49 | LUCES ARRI 650 | 1 |
| 50 | LUCES LED BICOLOR LUME CUBE | 1 |
| 51 | LUCES VIDPRO LED-230 | 1 |
| 52 | MIC DURO SENNHEISER E835 | 1 |
| 53 | MICRO BOOM AUDIOTECNICA AT875R | 1 |
| 54 | MICRO BOOM SENHESSER MKH70P48 | 1 |
| 55 | MICRO BOOM SENNHEISER ME66 Sk6 | 1 |
| 56 | MICROFONO HDR- 10 POLSEN | 1 |
| 57 | RONIN DJI R51 | 1 |
| 58 | SHOULDERPOD IQ IRIG | 1 |
| 59 | SHOULDERPOD MOVO | 1 |
| 60 | SOPORTE HOMBRO SMALLRIG | 1 |
| 61 | TRIPODE AVENGER CENTURY | 1 |
| 62 | TRIPODE LIBEC ALX H + SLIDER | 1 |
| 63 | TRIPODE LIBEC ALX S8 + SLIDER | 1 |
| 64 | TRIPODE LIBEC H60 | 1 |
| 65 | TRIPODE LIBEC TH-Z | 1 |
| 66 | TRIPODE MAGNUS PV-3310 | 1 |
| 67 | TRIPODE MAGNUS PV-3310G | 1 |
| 68 | TRIPODE MANFROTO | 1 |

## Activos en formato de importación

La misma lista de arriba, en el mismo orden (fila 1 = activo #1), con las columnas de la plantilla de **Activos > Importar** (`flask-app/app/importar_activos.py`). Para importarla, se copian las filas a la hoja Activos de la plantilla o a un .csv.

- **placa**: provisional. Delta no muestra la placa real, y el importador exige una de 8 dígitos para cada equipo sin padre; se usó la serie `90000010`–`90000680` para que no choque con las del seed (`100000x0`). Hay que reemplazarla por la placa real antes de importar.
- **nombre**: el de Delta escrito legible y con la ortografía corregida (MICROFO → Micrófono, SENHESSER → Sennheiser, MANFROTO → Manfrotto, AUDIOTECNICA → Audio-Technica).
- **categoria**: `grabación audiovisual` para todos, igual que el seed. **subcategoria**: las del seed (cámara, lente, trípode, micrófono, luces) más adaptador, audífonos, estabilizador, grabadora, preamplificador y soporte. "Flex" queda sin subcategoría porque Delta no dice qué es.
- **marca/modelo**: solo lo que se deduce del nombre en Delta; vacío si no se sabe.
- Ninguno tiene padre ni lugar: Delta no dice qué lente va con qué cámara ni en qué espacio está cada cosa.

| tipo | nombre | descripcion | ubicacion | placa | placa_padre | lugar | categoria | subcategoria | marca | modelo | identificador | requiere_confirmacion | estado |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| equipo | Adaptador Lightning a 3.5 mm |  |  | 90000010 |  |  | grabación audiovisual | adaptador |  |  |  | no | disponible |
| equipo | Adaptador USB-C a 3.5 mm |  |  | 90000020 |  |  | grabación audiovisual | adaptador |  |  |  | no | disponible |
| equipo | Audífonos AKG K-240 Studio |  |  | 90000030 |  |  | grabación audiovisual | audífonos | AKG | K-240 Studio |  | no | disponible |
| equipo | Cámara Canon T5i |  |  | 90000040 |  |  | grabación audiovisual | cámara | Canon | T5i |  | no | disponible |
| equipo | Cámara Canon T6i |  |  | 90000050 |  |  | grabación audiovisual | cámara | Canon | T6i |  | no | disponible |
| equipo | Cámara Canon T7i |  |  | 90000060 |  |  | grabación audiovisual | cámara | Canon | T7i |  | no | disponible |
| equipo | Cámara Canon T8i |  |  | 90000070 |  |  | grabación audiovisual | cámara | Canon | T8i |  | no | disponible |
| equipo | Cámara de video GoPro Hero 4 |  |  | 90000080 |  |  | grabación audiovisual | cámara | GoPro | Hero 4 |  | no | disponible |
| equipo | Cámara de video Sony Z150 |  |  | 90000090 |  |  | grabación audiovisual | cámara | Sony | Z150 |  | no | disponible |
| equipo | Cámara Insta360 One X2 |  |  | 90000100 |  |  | grabación audiovisual | cámara | Insta360 | One X2 |  | no | disponible |
| equipo | Cámara Sony A7III |  |  | 90000110 |  |  | grabación audiovisual | cámara | Sony | A7III |  | no | disponible |
| equipo | Estabilizador DJI Osmo Mobile 2 |  |  | 90000120 |  |  | grabación audiovisual | estabilizador | DJI | Osmo Mobile 2 |  | no | disponible |
| equipo | Estabilizador DJI Osmo Mobile 4 |  |  | 90000130 |  |  | grabación audiovisual | estabilizador | DJI | Osmo Mobile 4 |  | no | disponible |
| equipo | Estabilizador DJI Osmo Mobile 6 |  |  | 90000140 |  |  | grabación audiovisual | estabilizador | DJI | Osmo Mobile 6 |  | no | disponible |
| equipo | Flex |  |  | 90000150 |  |  | grabación audiovisual |  |  |  |  | no | disponible |
| equipo | Fresnel Aputure F10 |  |  | 90000160 |  |  | grabación audiovisual | luces | Aputure | F10 |  | no | disponible |
| equipo | Grabadora Edirol R4 |  |  | 90000170 |  |  | grabación audiovisual | grabadora | Edirol | R4 |  | no | disponible |
| equipo | Grabadora Zoom P4 Podcast |  |  | 90000180 |  |  | grabación audiovisual | grabadora | Zoom | P4 |  | no | disponible |
| equipo | Grabadora Tascam DR-40 |  |  | 90000190 |  |  | grabación audiovisual | grabadora | Tascam | DR-40 |  | no | disponible |
| equipo | Grabadora Tascam DR-40X |  |  | 90000200 |  |  | grabación audiovisual | grabadora | Tascam | DR-40X |  | no | disponible |
| equipo | Soporte IK Multimedia iKlip A/V |  |  | 90000210 |  |  | grabación audiovisual | soporte | IK Multimedia | iKlip A/V |  | no | disponible |
| equipo | Micrófono duro IK Multimedia iRig Mic |  |  | 90000220 |  |  | grabación audiovisual | micrófono | IK Multimedia | iRig Mic |  | no | disponible |
| equipo | Micrófono de solapa IK Multimedia iRig Mic Lav |  |  | 90000230 |  |  | grabación audiovisual | micrófono | IK Multimedia | iRig Mic Lav |  | no | disponible |
| equipo | Preamplificador IK Multimedia iRig Pre |  |  | 90000240 |  |  | grabación audiovisual | preamplificador | IK Multimedia | iRig Pre |  | no | disponible |
| equipo | Kit de luces Aputure 300 |  |  | 90000250 |  |  | grabación audiovisual | luces | Aputure | 300 |  | no | disponible |
| equipo | Kit de luces GVM |  |  | 90000260 |  |  | grabación audiovisual | luces | GVM |  |  | no | disponible |
| equipo | Kit de luces Intellytech 120W |  |  | 90000270 |  |  | grabación audiovisual | luces | Intellytech | 120W |  | no | disponible |
| equipo | Kit de luces Intellytech 160W |  |  | 90000280 |  |  | grabación audiovisual | luces | Intellytech | 160W |  | no | disponible |
| equipo | Kit de luces Lume Cube |  |  | 90000290 |  |  | grabación audiovisual | luces | Lume Cube |  |  | no | disponible |
| equipo | Kit de lentes Beastgrip |  |  | 90000300 |  |  | grabación audiovisual | lente | Beastgrip |  |  | no | disponible |
| equipo | Kit de lentes para celular Mactrem |  |  | 90000310 |  |  | grabación audiovisual | lente | Mactrem |  |  | no | disponible |
| equipo | Kit micrófono Hollyland Lark M2 |  |  | 90000320 |  |  | grabación audiovisual | micrófono | Hollyland | Lark M2 |  | no | disponible |
| equipo | Kit micrófono boom Sennheiser ME67 |  |  | 90000330 |  |  | grabación audiovisual | micrófono | Sennheiser | ME67 |  | no | disponible |
| equipo | Kit micrófono de solapa Sennheiser |  |  | 90000340 |  |  | grabación audiovisual | micrófono | Sennheiser |  |  | no | disponible |
| equipo | Kit micrófono AVX |  |  | 90000350 |  |  | grabación audiovisual | micrófono |  | AVX |  | no | disponible |
| equipo | Lente Canon EF-S 10-18 mm |  |  | 90000360 |  |  | grabación audiovisual | lente | Canon | EF-S 10-18 mm |  | no | disponible |
| equipo | Lente Canon 17-40 mm |  |  | 90000370 |  |  | grabación audiovisual | lente | Canon | 17-40 mm |  | no | disponible |
| equipo | Lente Canon 18-55 mm |  |  | 90000380 |  |  | grabación audiovisual | lente | Canon | 18-55 mm |  | no | disponible |
| equipo | Lente Canon 50 mm |  |  | 90000390 |  |  | grabación audiovisual | lente | Canon | 50 mm |  | no | disponible |
| equipo | Lente Canon 55-250 mm |  |  | 90000400 |  |  | grabación audiovisual | lente | Canon | 55-250 mm |  | no | disponible |
| equipo | Lente Canon 75-300 mm |  |  | 90000410 |  |  | grabación audiovisual | lente | Canon | 75-300 mm |  | no | disponible |
| equipo | Lente Rokinon 16 mm |  |  | 90000420 |  |  | grabación audiovisual | lente | Rokinon | 16 mm |  | no | disponible |
| equipo | Lente Rokinon 24 mm |  |  | 90000430 |  |  | grabación audiovisual | lente | Rokinon | 24 mm |  | no | disponible |
| equipo | Lente Rokinon 44 mm |  |  | 90000440 |  |  | grabación audiovisual | lente | Rokinon | 44 mm |  | no | disponible |
| equipo | Lente Rokinon 50 mm |  |  | 90000450 |  |  | grabación audiovisual | lente | Rokinon | 50 mm |  | no | disponible |
| equipo | Lente Rokinon 85 mm |  |  | 90000460 |  |  | grabación audiovisual | lente | Rokinon | 85 mm |  | no | disponible |
| equipo | Luces Arri 1000 |  |  | 90000470 |  |  | grabación audiovisual | luces | Arri | 1000 |  | no | disponible |
| equipo | Luces Arri 300 |  |  | 90000480 |  |  | grabación audiovisual | luces | Arri | 300 |  | no | disponible |
| equipo | Luces Arri 650 |  |  | 90000490 |  |  | grabación audiovisual | luces | Arri | 650 |  | no | disponible |
| equipo | Luces LED bicolor Lume Cube |  |  | 90000500 |  |  | grabación audiovisual | luces | Lume Cube | LED bicolor |  | no | disponible |
| equipo | Luces Vidpro LED-230 |  |  | 90000510 |  |  | grabación audiovisual | luces | Vidpro | LED-230 |  | no | disponible |
| equipo | Micrófono duro Sennheiser E835 |  |  | 90000520 |  |  | grabación audiovisual | micrófono | Sennheiser | E835 |  | no | disponible |
| equipo | Micrófono boom Audio-Technica AT875R |  |  | 90000530 |  |  | grabación audiovisual | micrófono | Audio-Technica | AT875R |  | no | disponible |
| equipo | Micrófono boom Sennheiser MKH70 P48 |  |  | 90000540 |  |  | grabación audiovisual | micrófono | Sennheiser | MKH70 P48 |  | no | disponible |
| equipo | Micrófono boom Sennheiser ME66 SK6 |  |  | 90000550 |  |  | grabación audiovisual | micrófono | Sennheiser | ME66 SK6 |  | no | disponible |
| equipo | Micrófono Polsen HDR-10 |  |  | 90000560 |  |  | grabación audiovisual | micrófono | Polsen | HDR-10 |  | no | disponible |
| equipo | Estabilizador DJI Ronin R51 |  |  | 90000570 |  |  | grabación audiovisual | estabilizador | DJI | Ronin R51 |  | no | disponible |
| equipo | Shoulderpod iQ iRig |  |  | 90000580 |  |  | grabación audiovisual | soporte |  | iQ iRig |  | no | disponible |
| equipo | Shoulderpod Movo |  |  | 90000590 |  |  | grabación audiovisual | soporte | Movo |  |  | no | disponible |
| equipo | Soporte de hombro SmallRig |  |  | 90000600 |  |  | grabación audiovisual | soporte | SmallRig |  |  | no | disponible |
| equipo | Trípode Avenger Century |  |  | 90000610 |  |  | grabación audiovisual | trípode | Avenger | Century |  | no | disponible |
| equipo | Trípode Libec ALX H + slider |  |  | 90000620 |  |  | grabación audiovisual | trípode | Libec | ALX H |  | no | disponible |
| equipo | Trípode Libec ALX S8 + slider |  |  | 90000630 |  |  | grabación audiovisual | trípode | Libec | ALX S8 |  | no | disponible |
| equipo | Trípode Libec H60 |  |  | 90000640 |  |  | grabación audiovisual | trípode | Libec | H60 |  | no | disponible |
| equipo | Trípode Libec TH-Z |  |  | 90000650 |  |  | grabación audiovisual | trípode | Libec | TH-Z |  | no | disponible |
| equipo | Trípode Magnus PV-3310 |  |  | 90000660 |  |  | grabación audiovisual | trípode | Magnus | PV-3310 |  | no | disponible |
| equipo | Trípode Magnus PV-3310G |  |  | 90000670 |  |  | grabación audiovisual | trípode | Magnus | PV-3310G |  | no | disponible |
| equipo | Trípode Manfrotto |  |  | 90000680 |  |  | grabación audiovisual | trípode | Manfrotto |  |  | no | disponible |
