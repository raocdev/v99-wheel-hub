# Protocolo BLE do PXN V99

Comandos confirmados num volante real (firmware 67) com Windows 11 e a biblioteca Python `bleak`.

## Conexão

| Item | Valor |
|---|---|
| Nome anunciado | `PXN-V99` (comparação sem diferenciar maiúsculas) |
| Tipo | BLE / GATT, sem pareamento do sistema (`connectGatt` direto) |
| Serviço | `0000ffe0-0000-1000-8000-00805f9b34fb` |
| Escrita (app → volante) | característica `0000ffe1-...` |
| Notificação (volante → app) | característica `0000ffe2-...` (ativar CCCD `00002902-...`) |

Fluxo: escanear → conectar → descobrir serviços → ativar notificação em `ffe2` → pronto.
Mandar **um comando por vez** e esperar a resposta (ou ~100 ms) antes do próximo.
Não há checksum nem cabeçalho extra: o primeiro byte é o "report ID".

## Comandos (app → volante, em `ffe1`)

`M` = modo/perfil atual (vem do `readState`).

| Função | Bytes |
|---|---|
| Ler estado | `50 01` |
| Ler perfil (parte `p` = 0..5) | `51 M p` |
| Gravar perfil (parte `p`) | `52 M p <17 bytes>` — total 20 bytes, ver abaixo |
| Trocar modo | `53 01 modo` |
| Mapear botão | `53 02 M botao destino` |
| Força do FFB | `53 03 M ffb reverso` |
| Ângulo de rotação | `53 04 M 01 angLo angHi` (uint16 little-endian) |
| Resetar perfil | `53 08 M` |
| Forças (mola, amortecimento, atrito, inércia) | `53 09 01 mola amort atrito inercia` |
| Modo de teste liga/desliga | `54 01` / `54 02` |

## Respostas (volante → app, em `ffe2`)

| 1º byte | Significado |
|---|---|
| `D0` | Estado: `[2]` modo, `[3]` versão menor, `[4]` versão maior, `[5]` pedal, `[6]` alavanca, `[7]` modo de ângulo, `[8]` freio |
| `D1` | Parte de perfil (`[2]` = número da parte; formato igual ao de gravação) |
| `D2` | Confirmação de gravação de perfil |
| `D3` (ou `53`) | Eco do parâmetro alterado; `[1]` é o subcomando (1, 2, 3, 4, 6, 7, 8, 9, 14) |
| `D4 03 ...` | Dados ao vivo do modo de teste (~33 por segundo) |

## Perfil (6 partes de 20 bytes)

Bytes `[0]=52/D1`, `[1]=modo`, `[2]=parte`, dados a partir do `[3]`. Inteiros de 16 bits em little-endian.

| Parte | Conteúdo |
|---|---|
| 0 | Mapeamento de botões 0–16 (17 bytes) |
| 1 | Mapeamento de botões 17–33 (17 bytes) |
| 2 | 2 botões finais, ângulo (u16), ffb, mola, amortecimento, atrito, inércia, ffb reverso |
| 3 | Curva do acelerador: 4 pontos (x u16, y u16) |
| 4 | Curva do freio: 4 pontos |
| 5 | Curva da embreagem: 4 pontos |

## Confirmado no volante real (2026-10-29, `v99_ler.py`, Windows + bleak)

- Endereço `DC:32:62:19:00:03`; `ffe1` = read/notify/write, `ffe2` = notify/write. Escrita com resposta funciona.
- `50 01` → `D0 01 01 43 00 02 02 01 00 ...` (20 bytes): modo 1, versão 0x0043 (67), pedal 2, alavanca 2, modo de ângulo 1, freio 0.
- `51 01 p` responde as 6 partes. Parte 2: ângulo 900, FFB 100, mola/amortecimento/atrito/inércia 50, reverso 0. Mapeamento de botões = identidade (padrão). Curvas 3–5 vieram todas zeradas (padrão linear ou não usado no V99?).

### Gravação de ângulo (`v99_teste_angulo.py`)

- `53 04 01 01 1C 02` (540°) → eco `D3 04 01 01 1C 02 ...`; `53 04 01 01 84 03` (900°) → eco `D3 04 01 01 84 03 ...`. A gravação é aceita e o eco devolve o valor.
- Um `51 M 02` enviado ~0,5 s depois de gravar **não teve resposta** (as duas vezes). Numa conexão nova a leitura volta a funcionar e mostra 900° (restaurado).
  Hipótese: o volante fica ocupado após gravar (salvando na memória?) ou ignora leitura de perfil logo após edição. Usar o eco `D3` como confirmação, ou esperar mais antes de reler.

### App de configuração (`v99_app.py`)

- Faixas usadas pelo programa: ângulo 90–900, FFB 0–100, mola/amortecimento/atrito/inércia 0–100, todos em passos de 10. As forças só valem nos modos 1 (PC DInput) e 5 (PS4).
- Modos: 1 PC DInput, 2 PC XInput, 5 PS4, 8 Xbox One, 10 Android, 11 Switch Pro.
- `53 03` (FFB/reverso) e `53 09` (forças, byte 2 fixo `01`) confirmados com eco `D3`.
- Cerca de 1 em 3 comandos seguidos fica **sem eco**; às vezes o comando foi aplicado mesmo assim (ângulo 540° apareceu depois de reconectar). Reenviar é seguro porque os comandos são de valor absoluto: o app reenvia até 4× (0,8 s de espera cada).
- Os valores **ficam salvos** no volante: continuam após desconectar e reconectar.

### Mapeamento, curvas, modo e reset (testado 2026-10-29)

- **Mapeamento** `53 02 M origem destino` → eco `D3 02 M origem destino`. "Restaurar" = destino igual à origem (o programa usa o valor 46 só na tela, como "padrão"). Testado: A(10)→B(9) e volta.
  Botões por grupo: volante `14 12 13 15 4 6 7 5 10 9 11 8 1 2 16 0 17 3`, pedais `22 21 20`, câmbio `25–33`, freio de mão `23`. Destinos: controle 0–17, pedais/câmbio 20–33, mouse 48–50, teclado 64–156 (nomes na tabela `NOMES` de `app_v99.html`).
- **Curvas** `52 M parte 4×(x u16, y u16)` (20 bytes; parte 3 acelerador, 4 freio, 5 embreagem) → resposta `D2 M parte`. x = 0–4095 (entrada), y = 0–65535 (saída); os 4 pontos se movem livremente mas sem se cruzar. Linear do app = `(0,0) (1365,21845) (2730,43690) (4095,65535)`. Curva zerada = nada gravado. Testado: gravada e relida (21844 por arredondamento).
- **Modo** `53 01 modo` → eco `D3 01 modo` + quadro extra `53 01 modo ...`. Só se troca entre 1 (DInput) e 2 (XInput), e só quando o modo atual é um desses; PS4/Xbox são automáticos. **Cada modo tem o seu perfil** → reler tudo depois de trocar. Testado 1→2→1; o BLE continua conectado.
- **Reset** `53 08 M` → eco `D3 08`; reler o perfil depois. Testado no modo 1: padrão de fábrica = ângulo 900°, FFB 100, reverso 0, mola/amortecimento/atrito/inércia **80**, botões no padrão. **As curvas não são alteradas** pelo reset.

### Botão físico de ângulo (testado 2026-10-29)

- Ao usar o botão de ângulo do volante, ele **envia sozinho** `53 04 M 00 lo hi` (ex.: `53 04 01 00 0E 01` = 270°) e o estado `D0` passa a ter modo de ângulo (`[7]`) = **0** (0 = definido pelo botão, 1 = definido pelo app).
- Mesmo no modo 0, `53 04 M 01 lo hi` continua mudando o ângulo (testado 540° e volta a 270°); o eco devolve o modo 0 e o modo continua 0.
- Visto uma vez: eco `D3 04` misturado com um quadro ao vivo (`D3 04 01 1A 14 D4 03 …`). O app só aceita como confirmação um eco que devolva os mesmos valores enviados.

### Acessórios ligados/desligados (testado 2026-10-30)

- O volante avisa sozinho quando um acessório é conectado ou desconectado: `53 06 M estado` (pedais), `53 07 M estado` (câmbio) e `53 0E M estado` (freio de mão; não testado). Estado `00` = desconectado, `02` = conectado (mesmo valor de `D0` `[5]`/`[6]`/`[8]`).
- Visto: `53 07 01 00 …` e `53 06 01 00 …` ao tirar os cabos; `53 06 01 02 …` e `53 07 01 02 …` ao ligar de novo.

## Pendências

- **USB (testado 2026-10-29):** o V99 aparece como `11FF:3245`. Interface 0 = joystick com FFB (PID, página 0x000F) e um relatório vendor **0x64** (página 0xFF01: saída 62 bytes, entrada 47 bytes). Interface 1 = teclado + mouse. Não existem relatórios 0x50–0x54 no descritor.
  Enviar `64 50 01 00…` é aceito pelo Windows, mas o volante **não responde** no 0x64. O formato desse canal é desconhecido. Não sondar o 0x64 às cegas: canais vendor costumam ter comandos de atualização de firmware.
