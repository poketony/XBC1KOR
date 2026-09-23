"""Wii CX LH decoder derived from the supplied main.dol's 0x80308570 routine.

This 0x40 stream is NOT the DS LZ40 format. 9-bit and 5-bit Huffman trees
encode literal/length symbols and backreference distance bit counts.
"""
from xeno_formats import num, need


class Bits:
    def __init__(self, data, at):
        self.data, self.pos, self.value, self.count = data, at, 0, 0

    def read(self, count):
        while self.count < count:
            self.value = (self.value << 8) | num(self.data, self.pos, 1)
            self.pos += 1
            self.count += 8
        self.count -= count
        result = (self.value >> self.count) & ((1 << count)-1)
        self.value &= (1 << self.count)-1
        return result


def tree(reader, bits, size_width):
    # Size prefix is little endian, whereas packed tree nodes are MSB-first.
    size = sum(reader.read(8) << (8*i) for i in range(size_width))
    count = (size+1)*32 - size_width*8
    result = [0] + [reader.read(bits) for _ in range(count//bits)]
    reader.read(count % bits)
    return result


def symbol(reader, table, bits):
    index = 1
    for _ in range(len(table)):
        need(index < len(table), 'LH tree node out of bounds')
        node = table[index]
        branch = reader.read(1)
        child = (index & ~1) + 2*((node & ((1 << (bits-2))-1))+1) + branch
        need(child < len(table), 'LH tree child out of bounds')
        if node & ((1 << (bits-1)) >> branch):
            return table[child]
        index = child
    raise ValueError('LH tree cycle')


def decompress(data, limit=64*1024*1024):
    need(data[:1] == b'\x40', 'LH signature')
    size, at = num(data, 1, 3), 4
    if not size:
        size, at = num(data, 4), 8
    need(0 < size <= limit, 'LH allocation limit')
    r = Bits(data, at)
    literals, distances = tree(r, 9, 2), tree(r, 5, 1)
    out = bytearray()
    while len(out) < size:
        value = symbol(r, literals, 9)
        if value < 256:
            out.append(value)
        else:
            count = (value & 255)+3
            b = symbol(r, distances, 5)
            offset = b if b <= 1 else (1 << (b-1)) | r.read(b-1)
            distance = offset+1
            need(distance <= len(out), 'LH invalid backreference')
            need(len(out)+count <= size, 'LH output overflow')
            while count:
                n = min(count, distance)
                start = len(out)-distance
                out.extend(out[start:start+n])
                count -= n
    return bytes(out), r.pos
