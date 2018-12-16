#!/usr/bin/python
'''
lzss compression and decompression

Ported fairly directly from the lzss.cpp source code
'''

import struct


LITERAL_NBITS = 9
CONTROL_LITERAL = 0
CONTROL_CODE = 1

# ----------------------------------------------------------------------
# Compression is implemented as a class because the output operation
# maintains the state of a partially filled output byte


class LzssCompressor(object):
    '''
    Implements LZSS compression

    >>> c = LzssCompressor(offset_length, code_length, min_match_length)
    >>> data = 'Some string to compress'
    >>> compdata = c.compress(data)
    
    '''
    def __init__(self, offset_len = 14, code_len = 3, min_len = 3):
        self.offset_len = offset_len
        self.code_len = code_len
        self.min_len = min_len
        self.max_len = (1 << self.code_len) - 1 + self.min_len
        # Compressor output state
        self.partial_output = 0
        self.partial_bitlen = 0
        self.output = []
    
    def _compress_write(self, dataBytes, data_bitlen):
        #print 'output: %x [%d]' % (dataBytes, data_bitlen)
        self.partial_output <<= data_bitlen
        self.partial_output |= dataBytes & ((1<<data_bitlen)-1)
        self.partial_bitlen += data_bitlen
        while self.partial_bitlen >= 8:
            b = (self.partial_output >> (self.partial_bitlen - 8)) & 0xFF
            #print 'appending %c [%02X]' % (chr(b), b)
            self.output.append(b)
            self.partial_output &= (1 << (self.partial_bitlen-8)) - 1
            self.partial_bitlen -= 8

    def _output_flush(self):
        if self.partial_bitlen > 0:
            self._compress_write(0, 8 - self.partial_bitlen)
        
    def _write_literal(self, literal):
        self._compress_write(CONTROL_LITERAL, 1)
        self._compress_write(ord(literal), LITERAL_NBITS-1)

    def _write_code(self, window_offset, window_len):
        self._compress_write(CONTROL_CODE, 1)
        self._compress_write(window_offset, self.offset_len)
        self._compress_write(window_len, self.code_len)
    
    def _compress_match(self, in_data, offset):
        '''Find the longest match for the string starting at offset in the preceeding data
        '''
        window_start = max(offset - (1 << self.offset_len), 0)
        
        for n in range(self.max_len, self.min_len-1, -1):
            window_end = min(offset + n, len(in_data))
            str_to_find = in_data[offset:window_end]
            idx = in_data.find(str_to_find, window_start, window_end-n)
            if idx != -1:
                code_offset = offset - idx - 1
                code_len = len(str_to_find)
                #if code_len > code_offset:
                #    print 'Edge Match @', offset, ':', code_offset, code_len 
                return (code_offset, code_len)
        
        return (0, 0)
    
    def compress(self, in_data):
        '''Compress a string of input data
        Returns: compressed output

        Compression parameters are in the first two bytes of output
        
        >>> input_data = 'Some string to compress'
        >>> output_data = compress(input_data)
        
        '''
        lens = (self.code_len << 4) | self.min_len & 0xf
        self.output = [ self.offset_len, lens ]
        
        code_nbits = 1 + self.offset_len + self.code_len
        
        idx = 0
        for n in range(min(self.min_len, len(in_data))):
            self._write_literal(in_data[idx])
            idx += 1
        
        while idx < len(in_data):
            # check two windows for the best match
            match1 = self._compress_match(in_data, idx)
            if match1[1] == self.max_len:
                match2 = (0, 0)
            else:
                match2 = self._compress_match(in_data, idx+1)

            if match1[1] < self.min_len and match2[1] < self.min_len:
                self._write_literal(in_data[idx])
                idx += 1
            elif match1[1] > match2[1]:
                self._write_code(match1[0], match1[1] - self.min_len)
                idx += match1[1]
            else:
                self._write_literal(in_data[idx])
                self._write_code(match2[0], match2[1] - self.min_len)
                idx += match2[1] + 1

        self._output_flush()
        return ''.join([chr(b) for b in self.output])


# ----------------------------------------------------------------------
# Decompression functions

def _decode(out, dataBytes, offset_len, code_len, min_len):
    offset_mask = (1 << offset_len) - 1
    code_mask = (1 << code_len) - 1
    offset = (dataBytes >> code_len) & offset_mask
    codelen = (dataBytes & code_mask) + min_len
    #print "decode: code=%X, offset=%d, len=%d" % (dataBytes, offset, codelen)
    # note: codelen can be > offset, so we modify out here rather than
    # returning a substring
    for i in range(codelen):
        #print 'appending (via decode):', '%c' % (chr(out[-offset-1]))
        out.append(out[-offset-1])
    # return out[-offset-1:-offset-1+codelen]

def _isLiteral(dataBytes, data_bitlen):
    return data_bitlen == LITERAL_NBITS and dataBytes & (1<<LITERAL_NBITS-1) == CONTROL_LITERAL


def decompress(in_data):
    '''Decompress a LZSS-compressed string of data
    Returns: decompressed output (as a string)

    Requires that the compression parameters are in the first two bytes of data
    
    >>> input_data = 'Some string to decompress'
    >>> output_data = decompress(input_data)
    
    
    '''
    (offset_len, lens) = struct.unpack('BB', in_data[0:2])
    code_len = (lens >> 4) & 0xf
    min_len = lens & 0xf
    code_nbits = 1 + offset_len + code_len
    
    out = []

    #print 'offset:', offset_len 
    #print 'code len:', code_len 
    #print 'min len:', min_len 
    
    curData = 0
    curDataBits = 0
    for d in in_data[2:]:
        
        # for each bit in the current data byte
        for n in range(7,-1,-1):
            curData <<= 1
            curDataBits += 1

            if ord(d) & (1<<n):
                curData |= 1

            #print 'data=%02X [%d]: %x  [literal=%d]' % (ord(d), curDataBits, curData, _isLiteral(curData, curDataBits))
                
            if _isLiteral(curData, curDataBits):
                #print 'appending:', '%c %02X' % (chr(curData), curData & 0xFF)
                out.append(curData & 0xFF)
                curData = 0
                curDataBits = 0

            elif curDataBits == code_nbits:
                #print 'data=%02X [%d]: %x  [literal=%d]' % (ord(d), curDataBits, curData, _isLiteral(curData, curDataBits))
                _decode(out, curData, offset_len, code_len, min_len)
                curData = 0
                curDataBits = 0

    return ''.join([chr(b) for b in out])



if __name__ == '__main__':
    import sys

    # TODO: read command line options for specifying compression parameters
    
    for in_file in sys.argv[1:]:
        c = LzssCompressor()
        try:
            inf = open(in_file, 'rb')
        except IOError:
            print "error: can not open '%s'" % in_file
            sys.exit(1)

        comp_file = in_file + '.lz'
        try:
            outf = open(comp_file, 'wb')
        except IOError:
            print "error: can not open '%s'" % comp_file
            sys.exit(1)

        in_data = inf.read()
        comp_out = c.compress(in_data)
        outf.write(comp_out)

        inf.close()
        outf.close()

    sys.exit(0)
        
