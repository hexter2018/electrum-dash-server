#!/usr/bin/env python
# Copyright(C) 2012 thomasv@gitorious

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see
# <http://www.gnu.org/licenses/agpl.html>.

"""
Todo:
   * server should check and return bitcoind status..
   * improve txpoint sorting
   * command to check cache

 mempool transactions do not need to be added to the database; it slows it down
"""

import abe_backend




import time, json, socket, operator, thread, ast, sys, re, traceback
import ConfigParser
from json import dumps, loads
import urllib
import threading

config = ConfigParser.ConfigParser()
# set some defaults, which will be overwritten by the config file
config.add_section('server')
config.set('server','banner', 'Welcome to Electrum!')
config.set('server', 'host', 'localhost')
config.set('server', 'port', '50000')
config.set('server', 'password', '')
config.set('server', 'irc', 'yes')
config.set('server', 'ircname', 'Electrum server')
config.add_section('database')
config.set('database', 'type', 'psycopg2')
config.set('database', 'database', 'abe')

try:
    f = open('/etc/electrum.conf','r')
    config.readfp(f)
    f.close()
except:
    print "Could not read electrum.conf. I will use the default values."

try:
    f = open('/etc/electrum.banner','r')
    config.set('server','banner', f.read())
    f.close()
except:
    pass


password = config.get('server','password')
stopping = False
sessions = {}



def random_string(N):
    import random, string
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(N))



def modified_addresses(a_session):
    #t1 = time.time()
    import copy
    session = copy.deepcopy(a_session)
    addresses = session['addresses']
    session['last_time'] = time.time()
    ret = {}
    k = 0
    for addr in addresses:
        status = store.get_status( addr )
        msg_id, last_status = addresses.get( addr )
        if last_status != status:
            addresses[addr] = msg_id, status
            ret[addr] = status

    #t2 = time.time() - t1 
    #if t2 > 10: print "high load:", session_id, "%d/%d"%(k,len(addresses)), t2
    return ret, addresses


def poll_session(session_id): 
    # native
    session = sessions.get(session_id)
    if session is None:
        print time.asctime(), "session not found", session_id
        return -1, {}
    else:
        sessions[session_id]['last_time'] = time.time()
        ret, addresses = modified_addresses(session)
        if ret: sessions[session_id]['addresses'] = addresses
        return repr( (store.block_number,ret))


def add_address_to_session(session_id, address):
    status = store.get_status(address)
    sessions[session_id]['addresses'][address] = ("", status)
    sessions[session_id]['last_time'] = time.time()
    return status


def new_session(version, addresses):
    session_id = random_string(10)
    sessions[session_id] = { 'addresses':{}, 'version':version }
    for a in addresses:
        sessions[session_id]['addresses'][a] = ('','')
    out = repr( (session_id, config.get('server','banner').replace('\\n','\n') ) )
    sessions[session_id]['last_time'] = time.time()
    return out


def update_session(session_id,addresses):
    """deprecated in 0.42, wad replaced by add_address_to_session"""
    sessions[session_id]['addresses'] = {}
    for a in addresses:
        sessions[session_id]['addresses'][a] = ''
    sessions[session_id]['last_time'] = time.time()
    return 'ok'


def native_server_thread():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((config.get('server','host'), config.getint('server','port')))
    s.listen(1)
    while not stopping:
        conn, addr = s.accept()
        try:
            thread.start_new_thread(native_client_thread, (addr, conn,))
        except:
            # can't start new thread if there is no memory..
            traceback.print_exc(file=sys.stdout)


def native_client_thread(ipaddr,conn):
    #print "client thread", ipaddr
    try:
        ipaddr = ipaddr[0]
        msg = ''
        while 1:
            d = conn.recv(1024)
            msg += d
            if not d: 
                break
            if '#' in msg:
                msg = msg.split('#', 1)[0]
                break
        try:
            cmd, data = ast.literal_eval(msg)
        except:
            print "syntax error", repr(msg), ipaddr
            conn.close()
            return

        out = do_command(cmd, data, ipaddr)
        if out:
            #print ipaddr, cmd, len(out)
            try:
                conn.send(out)
            except:
                print "error, could not send"

    finally:
        conn.close()


def timestr():
    return time.strftime("[%d/%m/%Y-%H:%M:%S]")

# used by the native handler
def do_command(cmd, data, ipaddr):

    if cmd=='b':
        out = "%d"%block_number

    elif cmd in ['session','new_session']:
        try:
            if cmd == 'session':
                addresses = ast.literal_eval(data)
                version = "old"
            else:
                version, addresses = ast.literal_eval(data)
                if version[0]=="0": version = "v" + version
        except:
            print "error", data
            return None
        print timestr(), "new session", ipaddr, addresses[0] if addresses else addresses, len(addresses), version
        out = new_session(version, addresses)

    elif cmd=='address.subscribe':
        try:
            session_id, addr = ast.literal_eval(data)
        except:
            traceback.print_exc(file=sys.stdout)
            print data
            return None
        out = add_address_to_session(session_id,addr)

    elif cmd=='update_session':
        try:
            session_id, addresses = ast.literal_eval(data)
        except:
            traceback.print_exc(file=sys.stdout)
            return None
        print timestr(), "update session", ipaddr, addresses[0] if addresses else addresses, len(addresses)
        out = update_session(session_id,addresses)
            
    elif cmd=='poll': 
        out = poll_session(data)

    elif cmd == 'h': 
        address = data
        out = repr( store.get_history( address ) )

    elif cmd =='tx':
        out = store.send_tx(data)
        print timestr(), "sent tx:", ipaddr, out

    elif cmd == 'peers':
        out = repr(irc.get_peers())

    else:
        out = None

    return out


def clean_session_thread():
    while not stopping:
        time.sleep(30)
        t = time.time()
        for k,s in sessions.items():
            if s.get('type') == 'persistent': continue
            t0 = s['last_time']
            if t - t0 > 5*60:
                sessions.pop(k)
                print "lost session", k
            

####################################################################


from processor import Shared, Processor, Dispatcher
from stratum_http import HttpServer
from stratum import TcpServer

class AbeProcessor(Processor):
    def process(self,request):
        message_id = request['id']
        method = request['method']
        params = request.get('params',[])
        #print request

        result = ''
        if method == 'numblocks.subscribe':
            result = store.block_number
        elif method == 'address.subscribe':
            address = params[0]
            store.watch_address(address)
            status = store.get_status(address)
            result = status
        elif method == 'client.version':
            #session.version = params[0]
            pass
        elif method == 'server.banner':
            result = config.get('server','banner').replace('\\n','\n')
        elif method == 'server.peers':
            result = irc.get_peers()
        elif method == 'address.get_history':
            address = params[0]
            result = store.get_history( address ) 
        elif method == 'transaction.broadcast':
            txo = store.send_tx(params[0])
            print "sent tx:", txo
            result = txo 
        else:
            print "unknown method", request

        if result!='':
            response = { 'id':message_id, 'method':method, 'params':params, 'result':result }
            self.push_response(response)

    def get_status(self,addr):
        return store.get_status(addr)



####################################################################



class Irc(threading.Thread):

    def __init__(self, processor):
        self.processor = processor
        threading.Thread.__init__(self)
        self.daemon = True
        self.peers = {}

    def get_peers(self):
        return self.peers.values()

    def run(self):
        NICK = 'E_'+random_string(10)
        while not self.processor.shared.stopped():
            try:
                s = socket.socket()
                s.connect(('irc.freenode.net', 6667))
                s.send('USER electrum 0 * :'+config.get('server','host')+' '+config.get('server','ircname')+'\n')
                s.send('NICK '+NICK+'\n')
                s.send('JOIN #electrum\n')
                sf = s.makefile('r', 0)
                t = 0
                while not self.processor.shared.stopped():
                    line = sf.readline()
                    line = line.rstrip('\r\n')
                    line = line.split()
                    if line[0]=='PING': 
                        s.send('PONG '+line[1]+'\n')
                    elif '353' in line: # answer to /names
                        k = line.index('353')
                        for item in line[k+1:]:
                            if item[0:2] == 'E_':
                                s.send('WHO %s\n'%item)
                    elif '352' in line: # answer to /who
                        # warning: this is a horrible hack which apparently works
                        k = line.index('352')
                        ip = line[k+4]
                        ip = socket.gethostbyname(ip)
                        name = line[k+6]
                        host = line[k+9]
                        self.peers[name] = (ip,host)
                    if time.time() - t > 5*60:
                        self.processor.push_response({'method':'server.peers', 'result':[self.get_peers()]})
                        s.send('NAMES #electrum\n')
                        t = time.time()
                        self.peers = {}
            except:
                traceback.print_exc(file=sys.stdout)
            finally:
                sf.close()
                s.close()




if __name__ == '__main__':

    if len(sys.argv)>1:
        import jsonrpclib
        server = jsonrpclib.Server('http://%s:8081'%config.get('server','host'))
        cmd = sys.argv[1]
        if cmd == 'load':
            out = server.load(password)
        elif cmd == 'peers':
            out = server.server.peers()
        elif cmd == 'stop':
            out = server.stop(password)
        elif cmd == 'clear_cache':
            out = server.clear_cache(password)
        elif cmd == 'get_cache':
            out = server.get_cache(password,sys.argv[2])
        elif cmd == 'h':
            out = server.address.get_history(sys.argv[2])
        elif cmd == 'tx':
            out = server.transaction.broadcast(sys.argv[2])
        elif cmd == 'b':
            out = server.numblocks.subscribe()
        else:
            out = "Unknown command: '%s'" % cmd
        print out
        sys.exit(0)

    # backend
    store = abe_backend.AbeStore(config)

    # old protocol
    thread.start_new_thread(native_server_thread, ())
    thread.start_new_thread(clean_session_thread, ())

    processor = AbeProcessor()
    shared = Shared()
    # Bind shared to processor since constructor is user defined
    processor.shared = shared
    processor.start()
    # dispatcher
    dispatcher = Dispatcher(shared, processor)
    dispatcher.start()
    # Create various transports we need
    transports = [ TcpServer(shared, processor, "ecdsa.org",50001),
                   HttpServer(shared, processor, "ecdsa.org",8081)
                   ]
    for server in transports:
        server.start()


    if (config.get('server','irc') == 'yes' ):
	irc = Irc(processor)
        irc.start()


    print "starting Electrum server"
    store.run(processor)
    print "server stopped"

