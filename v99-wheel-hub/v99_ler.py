"""Leitura (somente) do estado e do perfil do volante V99 via BLE.

Envia apenas os comandos de leitura 50 01 (estado) e 51 M p (perfil).
Nenhum comando de gravação (52/53/54) é enviado.
Protocolo: ver PROTOCOLO_V99.md.
"""

import asyncio
import struct
import sys

from bleak import BleakClient, BleakScanner

NOME = "PXN-V99"
SERVICO = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_ESCRITA = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFICA = "0000ffe2-0000-1000-8000-00805f9b34fb"

# Ordem dos botões nas partes 0..2 do perfil (47 = posição sem botão)
KEYMAP_SEQUENCE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 17, 16, 47, 14,
                   15, 47, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35]


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def decodifica_estado(b: bytes) -> dict:
    return {
        "modo": b[2],
        "firmware": f"{b[4]}.{b[3]}",
        "pedal": b[5],
        "alavanca": b[6],
        "modo_angulo": b[7],
        "freio": b[8],
    }


def decodifica_parte(b: bytes) -> dict:
    parte = b[2]
    if parte in (0, 1):
        base = 0 if parte == 0 else 17
        return {"botoes": {KEYMAP_SEQUENCE[base + i]: b[3 + i] for i in range(17)}}
    if parte == 2:
        return {
            "botoes": {KEYMAP_SEQUENCE[34 + i]: b[3 + i] for i in range(2)},
            "angulo": struct.unpack_from("<h", b, 5)[0],
            "ffb": b[7],
            "mola": b[8],
            "amortecimento": b[9],
            "atrito": b[10],
            "inercia": b[11],
            "ffb_reverso": struct.unpack_from("<b", b, 12)[0],
        }
    nomes = {3: "curva_acelerador", 4: "curva_freio", 5: "curva_embreagem"}
    pontos = [struct.unpack_from("<HH", b, 3 + 4 * i) for i in range(4)]
    return {nomes.get(parte, f"parte_{parte}"): pontos}


async def main() -> int:
    print(f"Procurando '{NOME}' por até 15 s (feche o app do celular)...")
    dispositivo = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "").upper() == NOME, timeout=15.0
    )
    if dispositivo is None:
        print("Não encontrado. Dispositivos com 'PXN' no nome vistos agora:")
        for d in await BleakScanner.discover(timeout=8.0):
            if d.name and "PXN" in d.name.upper():
                print(f"  {d.name}  {d.address}")
        return 1
    print(f"Encontrado: {dispositivo.name} ({dispositivo.address})")

    respostas: asyncio.Queue[bytes] = asyncio.Queue()

    def ao_notificar(_, dados: bytearray):
        dados = bytes(dados)
        print(f"  <- {hexs(dados)}")
        respostas.put_nowait(dados)

    async def espera(primeiro_byte: int, parte: int | None = None, timeout: float = 2.0):
        fim = asyncio.get_running_loop().time() + timeout
        while (resta := fim - asyncio.get_running_loop().time()) > 0:
            try:
                b = await asyncio.wait_for(respostas.get(), resta)
            except asyncio.TimeoutError:
                break
            if b and b[0] == primeiro_byte and (parte is None or (len(b) > 2 and b[2] == parte)):
                return b
        return None

    async with BleakClient(dispositivo) as cliente:
        char = cliente.services.get_characteristic(CHAR_ESCRITA)
        com_resposta = char is not None and "write" in char.properties
        print("Serviços/características do volante:")
        for s in cliente.services:
            for c in s.characteristics:
                print(f"  {c.uuid}  {','.join(c.properties)}")

        await cliente.start_notify(CHAR_NOTIFICA, ao_notificar)

        async def envia(cmd: bytes):
            print(f"  -> {hexs(cmd)}")
            await cliente.write_gatt_char(CHAR_ESCRITA, cmd, response=com_resposta)

        print("\n== Estado")
        await envia(bytes([0x50, 0x01]))
        estado = await espera(0xD0)
        if estado is None:
            print("Sem resposta ao pedido de estado.")
            return 2
        info = decodifica_estado(estado)
        for k, v in info.items():
            print(f"  {k}: {v}")

        print("\n== Perfil do modo", info["modo"])
        for parte in range(6):
            await envia(bytes([0x51, info["modo"], parte]))
            r = await espera(0xD1, parte)
            if r is None:
                print(f"  parte {parte}: sem resposta")
                continue
            for k, v in decodifica_parte(r).items():
                print(f"  {k}: {v}")

        await cliente.stop_notify(CHAR_NOTIFICA)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
