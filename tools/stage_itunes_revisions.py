#!/usr/bin/env python3
"""Stage experimental iTunes 12.13.11.1 Genius revision markers (Windows).

Reads a copied .itl and its matching *decrypted* host Genius SQLite database.
Writes a new .itl only; does not install it or change either input. The host
Genius database, its encryption/key, and Mix creation are separate prerequisites.
"""
import argparse
import ctypes
import os
import sqlite3
import struct
import sys
import zlib
from contextlib import closing
from pathlib import Path

ITL_KEY = b'BHUILuilfghuila3'
MASK64 = (1 << 64) - 1


def aes_ecb(data, decrypt=False):
    """AES ECB through Windows CNG; no third-party Python dependencies."""
    if sys.platform != 'win32':
        raise RuntimeError('The native .itl AES implementation requires Windows CNG')
    if len(data) % 16:
        raise ValueError('AES input must consist of complete blocks')
    lib = ctypes.WinDLL('bcrypt.dll')
    voidp = ctypes.c_void_p
    ulong = ctypes.c_ulong
    lib.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(voidp), ctypes.c_wchar_p, ctypes.c_wchar_p, ulong]
    lib.BCryptSetProperty.argtypes = [voidp, ctypes.c_wchar_p, voidp, ulong, ulong]
    lib.BCryptGenerateSymmetricKey.argtypes = [voidp, ctypes.POINTER(voidp), voidp, ulong, voidp, ulong, ulong]
    for method in ('BCryptEncrypt', 'BCryptDecrypt'):
        getattr(lib, method).argtypes = [voidp, voidp, ulong, voidp, voidp, ulong, voidp, ulong, ctypes.POINTER(ulong), ulong]
    lib.BCryptDestroyKey.argtypes = [voidp]
    lib.BCryptCloseAlgorithmProvider.argtypes = [voidp, ulong]
    for method in ('BCryptOpenAlgorithmProvider', 'BCryptSetProperty', 'BCryptGenerateSymmetricKey', 'BCryptEncrypt', 'BCryptDecrypt'):
        getattr(lib, method).restype = ctypes.c_long

    def check(status, op):
        if status != 0:
            raise OSError('%s failed: NTSTATUS 0x%08X' % (op, status & 0xffffffff))

    provider, key = voidp(), voidp()
    check(lib.BCryptOpenAlgorithmProvider(ctypes.byref(provider), 'AES', None, 0), 'open AES')
    try:
        mode = ctypes.create_unicode_buffer('ChainingModeECB')
        check(lib.BCryptSetProperty(provider, 'ChainingMode', mode, ctypes.sizeof(mode), 0), 'set ECB')
        secret = ctypes.create_string_buffer(ITL_KEY, len(ITL_KEY))
        check(lib.BCryptGenerateSymmetricKey(provider, ctypes.byref(key), None, 0, secret, len(ITL_KEY), 0), 'generate key')
        try:
            source = ctypes.create_string_buffer(data, len(data))
            target = ctypes.create_string_buffer(len(data))
            written = ulong()
            func = lib.BCryptDecrypt if decrypt else lib.BCryptEncrypt
            check(func(key, source, len(data), None, None, 0, target, len(data), ctypes.byref(written), 0), 'AES ECB')
            if written.value != len(data):
                raise ValueError('unexpected AES output length')
            return target.raw
        finally:
            lib.BCryptDestroyKey(key)
    finally:
        lib.BCryptCloseAlgorithmProvider(provider, 0)


def decode_itl(data):
    if len(data) < 0x60 or data[:4] != b'hdfm':
        raise ValueError('not a supported hdfm library')
    header_len, total_len = struct.unpack_from('>II', data, 4)
    max_crypt = struct.unpack_from('>I', data, 0x5c)[0]
    if header_len < 0x60 or total_len != len(data) or header_len > total_len:
        raise ValueError('unexpected hdfm bounds')
    payload = data[header_len:]
    size = (len(payload) // 16) * 16
    if max_crypt:
        size = min(max_crypt, size)
    if size % 16:
        raise ValueError('unsupported encryption length')
    plain = zlib.decompress(aes_ecb(payload[:size], decrypt=True) + payload[size:])
    return data[:header_len], bytearray(plain), max_crypt


def encode_itl(header, plain, max_crypt):
    payload = zlib.compress(bytes(plain), 1)
    size = (len(payload) // 16) * 16
    if max_crypt:
        size = min(max_crypt, size)
    if size % 16:
        raise ValueError('unsupported encryption length')
    out_header = bytearray(header)
    struct.pack_into('>I', out_header, 8, len(out_header) + len(payload))
    return bytes(out_header) + aes_ecb(payload[:size]) + payload[size:]


def u32(buf, offset):
    return struct.unpack_from('<I', buf, offset)[0]


def track_records(plain):
    """Parse validated msdh/mlth/mith boundaries; yield offsets, headers and IDs."""
    pos, track_blocks = 0, []
    while pos < len(plain):
        if pos + 16 > len(plain) or plain[pos:pos + 4] != b'msdh':
            raise ValueError('unexpected top-level record at %#x' % pos)
        header, total, kind = struct.unpack_from('<III', plain, pos + 4)
        if header < 16 or total < header or pos + total > len(plain):
            raise ValueError('invalid msdh bounds')
        if kind in (1, 13):
            track_blocks.append((kind, pos, header, total))
        pos += total
    main = [block for block in track_blocks if block[0] == 1]
    selected = main or [block for block in track_blocks if block[0] == 13]
    if len(selected) != 1:
        raise ValueError('expected exactly one primary track dataset')
    _, start, header, total = selected[0]
    end, sub = start + total, start + header
    if sub + 12 > end or plain[sub:sub + 4] != b'mlth':
        raise ValueError('missing mlth')
    mlth_header, count = struct.unpack_from('<II', plain, sub + 4)
    if mlth_header < 12 or sub + mlth_header > end:
        raise ValueError('invalid mlth bounds')
    pos = sub + mlth_header
    seen = set()
    for _ in range(count):
        if pos + 20 > end or plain[pos:pos + 4] != b'mith':
            raise ValueError('invalid mith at %#x' % pos)
        hdr, length, _, tid = struct.unpack_from('<IIII', plain, pos + 4)
        if hdr < 20 or length < hdr or pos + length > end or tid in seen:
            raise ValueError('invalid or duplicate track record')
        seen.add(tid)
        yield pos, hdr, tid
        pos += length
    if pos != end:
        raise ValueError('unexpected trailing track dataset content')


def read_graph(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Genius SQLite integrity check failed')
        result = []
        for table in ('genius_metadata', 'genius_similarities'):
            rows = {}
            for gid, blob in db.execute('SELECT genius_id, data FROM ' + table):
                if not isinstance(gid, int) or not isinstance(blob, bytes):
                    raise ValueError('invalid %s row' % table)
                gid &= MASK64
                if gid == 0 or gid in rows:
                    raise ValueError('zero or duplicate Genius ID in ' + table)
                rows[gid] = blob
            result.append(rows)
    return result


def stage(plain, metadata, similarities):
    pending, seen = [], set()
    for pos, hdr, tid in track_records(plain):
        if hdr < 0x1ec:
            raise ValueError('track %d has no Genius ID field' % tid)
        gid = struct.unpack_from('<Q', plain, pos + 0x1e4)[0]
        if not gid:
            continue
        if hdr < 0x1f4 or gid not in metadata or gid not in similarities:
            raise ValueError('missing revision field or graph rows for track %d' % tid)
        if gid in seen:
            raise ValueError('duplicate Genius ID %d' % gid)
        seen.add(gid)
        pending.append((pos + 0x1f0, zlib.crc32(metadata[gid] + similarities[gid]) or 1))
    if not pending:
        raise ValueError('no tracks have Genius IDs')
    result = bytearray(plain)
    for offset, value in pending:
        struct.pack_into('<I', result, offset, value)
    return result, len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--itl', required=True, help='copied iTunes Library.itl')
    parser.add_argument('--genius-plain', required=True, help='matching decrypted host Genius SQLite copy')
    parser.add_argument('--out', required=True, help='new staged .itl (must not exist)')
    args = parser.parse_args()
    if os.path.exists(args.out):
        parser.error('output must not already exist')
    header, plain, max_crypt = decode_itl(Path(args.itl).read_bytes())
    metadata, similarities = read_graph(args.genius_plain)
    changed, count = stage(plain, metadata, similarities)
    encoded = encode_itl(header, changed, max_crypt)
    _, verified, _ = decode_itl(encoded)
    if verified != changed:
        raise ValueError('staged output failed .itl round-trip verification')
    with open(args.out, 'xb') as output:
        output.write(encoded)
    print('Staged %d track revision markers to %s' % (count, args.out))
    print('Experimental output only; graph, playlist and deployment are separate steps.')


if __name__ == '__main__':
    main()
