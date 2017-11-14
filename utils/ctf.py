#!/usr/bin/env python
# encoding:utf-8

import os
import gzip
import signal
from cStringIO import StringIO

import pwn
from pwn import *

__all__ = [
    'factor', 'gcd', 'ext_euclid', 'rsa_decrypt',
    'unhex', 'ljust', 'rjust', 'gzipc', 'gzipd',
    'debug',
    'shellcode',
]

# export all imported from pwn
__all__ += [i for i in dir(pwn) if not i.startswith('__')]
__all__ = list(set(__all__))


#############################
### utils for calculation ###
#############################

def factor(n):
    """Integer factorization (Prime decomposition)."""
    while (2 < n) and (n & 1 == 0):
        n >>= 1
        print '2 * ',
    i = 3
    while i < n:
        if n % i == 0:
            n /= i
            print '%d *' % i,
            continue
        i += 2
    print n


def gcd(a, b):
    """Calculate greatest common divisor."""
    if b == 0:
        return a
    return gcd(b, a % b)


def ext_euclid(a, b):
    """Extended Euclidean algorithm. a > b, ax+by=GCD(a, b) => x,y"""
    if a % b == 0:
        return 0, 1
    x, y = ext_euclid(b, a % b)
    return y, x - a / b * y


def rsa_decrypt(c, e, p, q):
    """Decrypt RSA encrypted message when p and q are known."""
    # First calculate d.
    phi_n = (p - 1) * (q - 1)
    d, _ = ext_euclid(e, phi_n)
    d %= phi_n

    # Decrypt message using e.
    c_value = int(enhex(c), 16)
    n = p * q
    m = pow(c_value, d, n)
    m_hex = '%x' % m
    return unhex(m_hex)


#############################
### utils for EXP writing ###
#############################

def unhex(s):
    """Hex decode strings.
    Override unhex in pwntools.
    Hex-strings with odd length are acceptable.
    """
    s = str(s).strip()
    return (len(s) % 2 and '0' + s or s).decode('hex')


def ljust(s, n, c=None):
    assert len(s) <= n
    if c is None:
        return s + cyclic(n - len(s))
    else:
        return s.ljust(n, c)


def rjust(s, n, c=None):
    assert len(s) <= n
    if c is None:
        return cyclic(n - len(s)) + s
    else:
        return s.rjust(n, c)


def gzipc(s, compresslevel=9):
    io = StringIO()
    gp = gzip.GzipFile(mode='w', compresslevel=compresslevel, fileobj=io)
    gp.write(s)
    gp.close()
    io.seek(0)
    return io.read()


def gzipd(s):
    return gzip.GzipFile(fileobj=StringIO(s)).read()


#######################
### utils for debug ###
#######################

def debug(args, **kwargs):
    if type(args) == str:
        args = [args]
    io = process(['gdb', args[0]], **kwargs)
    io.debug_mode = True
    io.sendline('set prompt {0} '.format(term.text.bold_red('gdb$')))
    io.sendline('set args ' + ' '.join(args[1:]))
    return io


def _gdb_break(self, addr, need_interrupt=False):
    if need_interrupt:
        self.interrupt()
        _gdb_break(self, addr)
        self.c()
        return

    if type(addr) == list or type(addr) == tuple:
        for one in addr:
            _gdb_break(self, one)
    elif type(addr) == int or type(addr) == long:
        self.sendline('b *0x{0:x}'.format(addr))
    else:
        self.sendline('b {0}'.format(addr))


def _gdb_run(self):
    message = "Starting to run program %r" % self.program
    with log.progress(message) as p:
        self.sendline('r')
        while not proc.children(proc.pidof(self)[0]):
            sleep(0.01)


def _gdb_continue(self):
    message = "Continuing to run program %r" % self.program
    with log.progress(message) as p:
        self.sendline('c')
        self.recvline_endswith('Continuing.')


def _gdb_interrupt(self, timeout=0.1):
    if timeout:
        buf = self.recvrepeat(timeout)
        _gdb_interrupt(self, 0)
        # Make sure the process has been interrupted.
        buf += self.recvuntil(term.text.bold_red('gdb$'))
        self.unrecv(buf)
        return

    for child in proc.children(proc.pidof(self)[0]):
        os.kill(child, signal.SIGINT)


def _ext_interactive(self, prompt=term.text.bold_red('$') + ' '):
    """interactive(prompt = pwnlib.term.text.bold_red('$') + ' ')
    Does simultaneous reading and writing to the tube. In principle this just
    connects the tube to standard in and standard out, but in practice this
    is much more usable, since we are using :mod:`pwnlib.term` to print a
    floating prompt.
    Thus it only works in while in :data:`pwnlib.term.term_mode`.
    """

    def handler(signum, frame):
        self.interrupt(0)

    old_handler = signal.signal(signal.SIGINT, handler)

    log.info('Switching to extensive interactive mode')

    go = threading.Event()

    def recv_thread():
        while not go.isSet():
            try:
                cur = self.recv(timeout=0.05)
                if cur:
                    sys.stderr.write(cur)
                    sys.stderr.flush()
            except EOFError:
                log.info('Got EOF while reading in interactive')
                break

    t = context.Thread(target=recv_thread)
    t.daemon = True
    t.start()

    try:
        while not go.isSet():
            if term.term_mode:
                if self.debug_mode:
                    data = term.readline.readline(prompt='', float=True)
                else:
                    data = term.readline.readline(prompt=prompt, float=True)
            else:
                data = sys.stdin.readline()

            if data:
                # continue and exit interactive mode
                try:
                    if data.strip() == 'c!':
                        go.set()
                        sleep(0.05)
                        self.c()
                    else:
                        data = safeeval.const(
                            '"""{0}"""'.format(data.replace('"', r'\"')))
                        self.send(data)
                except ValueError:
                    log.warning('Illegal input, ignored!')
                except EOFError:
                    go.set()
                    log.info('Got EOF while sending in interactive')
            else:
                go.set()
    except KeyboardInterrupt:
        log.info('Interrupted')
        go.set()

    while t.is_alive():
        t.join(timeout=0.1)

    signal.signal(signal.SIGINT, old_handler)


def _send(self, data):
    self._send(str(data))


def _sendline(self, data):
    self._sendline(str(data))


def _sendlines(self, data):
    for row in data:
        self.sendline(row)


def _recvregex(self, regex, exact=False, group=None, **kwargs):
    """recvregex(regex, exact = False, timeout = default) -> str
    Wrapper around :func:`recvpred`, which will return when a regex
    matches the string in the buffer.
    By default :func:`re.RegexObject.search` is used, but if `exact` is
    set to True, then :func:`re.RegexObject.match` will be used instead.
    If the request is not satisfied before ``timeout`` seconds pass,
    all data is buffered and an empty string (``''``) is returned.
    """

    if isinstance(regex, (str, unicode)):
        regex = re.compile(regex)

    if exact:
        pred = regex.match
    else:
        pred = regex.search

    data = self.recvpred(pred, **kwargs)
    if group is None:
        return data
    match = pred(data)
    if hasattr(group, '__iter__'):
        return match.group(*group)
    return match.group(group)


def _recvline_regex(self, regex, exact=False, group=None, **kwargs):
    """recvregex(regex, exact = False, keepends = False, timeout = default) -> str
    Wrapper around :func:`recvline_pred`, which will return when a regex
    matches a line.
    By default :func:`re.RegexObject.search` is used, but if `exact` is
    set to True, then :func:`re.RegexObject.match` will be used instead.
    If the request is not satisfied before ``timeout`` seconds pass,
    all data is buffered and an empty string (``''``) is returned.
    """

    if isinstance(regex, (str, unicode)):
        regex = re.compile(regex)

    if exact:
        pred = regex.match
    else:
        pred = regex.search

    data = self.recvline_pred(pred, **kwargs)
    if group is None:
        return data
    match = pred(data)
    if hasattr(group, '__iter__'):
        return match.group(*group)
    return match.group(group)


pwnlib.tubes.tube.tube.debug_mode = False
pwnlib.tubes.tube.tube.b = _gdb_break
pwnlib.tubes.tube.tube.r = _gdb_run
pwnlib.tubes.tube.tube.c = _gdb_continue
pwnlib.tubes.tube.tube.interrupt = _gdb_interrupt

pwnlib.tubes.tube.tube._interactive = pwnlib.tubes.tube.tube.interactive
pwnlib.tubes.tube.tube.interactive = _ext_interactive
pwnlib.tubes.tube.tube._send = pwnlib.tubes.tube.tube.send
pwnlib.tubes.tube.tube.send = _send
pwnlib.tubes.tube.tube._sendline = pwnlib.tubes.tube.tube.sendline
pwnlib.tubes.tube.tube.sendline = _sendline
pwnlib.tubes.tube.tube.sendlines = _sendlines
pwnlib.tubes.tube.tube._recvregex = pwnlib.tubes.tube.tube.recvregex
pwnlib.tubes.tube.tube.recvregex = _recvregex
pwnlib.tubes.tube.tube._recvline_regex = pwnlib.tubes.tube.tube.recvline_regex
pwnlib.tubes.tube.tube.recvline_regex = _recvline_regex


#################################
### a short shellcode for x86 ###
#################################

# // al -> sys_execve
# // bx -> filename
# // cx -> args
# // dx -> env
# // "\xb0\x0b"                  // mov    $0xb,%al
# "\x6a\x0b"                  // push   $0xb
# "\x58"                      // pop    %eax
# "\x99"                      // cltd
# "\x31\xc9"                  // xor    %ecx,%ecx
# "\x52"                      // push   %edx
# "\x68\x2f\x2f\x73\x68"      // push   $0x68732f2f
# "\x68\x2f\x62\x69\x6e"      // push   $0x6e69622f
# "\x89\xe3"                  // mov    %esp,%ebx
# "\xcd\x80"                  // int    $0x80
#
# "\x6a\x0b\x58\x99\x31\xc9\x52\x68\x2f\x2f\x73\x68\x68\x2f\x62\x69\x6e\x89\xe3\xcd\x80"
# "j\x0bX\x991\xc9Rh//shh/bin\x89\xe3\xcd\x80"
# "j\x0b""X\x99""1\xc9""Rh//shh/bin\x89\xe3\xcd\x80"
#
# __asm__(
#     "push   $0xb        \n\t"
#     "pop    %eax        \n\t"
#     "cltd               \n\t"
#     "xor    %ecx,%ecx   \n\t"
#     "push   %edx        \n\t"
#     "push   $0x68732f2f \n\t"
#     "push   $0x6e69622f \n\t"
#     "mov    %esp,%ebx   \n\t"
#     "int    $0x80       \n\t"
# );

shellcode = 'j\x0bX\x991\xc9Rh//shh/bin\x89\xe3\xcd\x80'
