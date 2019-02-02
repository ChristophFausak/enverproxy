#!/usr/bin/python3
# This is a simple port-forward / proxy for EnvertecBridge

import socket
import select
import time
import sys
import os
import errno
import configparser
import ast
from slog import slog
from FHEM import FHEM

config = configparser.ConfigParser()
config['internal']={}
config['internal']['conf_file'] = '/etc/enverproxy.conf'
config['internal']['version']   = '1.1'
config['internal']['keys']      = "['buffer_size', 'delay', 'listen_port', 'DEBUG', 'forward_IP', 'forward_port', 'user', 'password', 'host', 'ID2device']"


class Forward:

    def __init__(self, l=None):
        if l == None:
            self.__log = slog('Forward class', True)
        else:
            self.__log = l    
        self.forward = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def start(self, host, port):
        try:
            self.forward.connect((host, port))
            return self.forward
        except OSError as e:
            self.__log.logMsg('Forward produced error: ' + str(e))
            return False


class TheServer:
    input_list = []
    channel = {}

    def __init__(self, host, port, l=None):
        if l == None:
            self.__log = slog('TheServer class', True)
        else:
            self.__log = l
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, port))
        self.server.listen(200)

    def main_loop(self):
        self.input_list.append(self.server)
        while True:
            if config['enverproxy']['DEBUG']:
                self.__log.logMsg('Entering main loop')
            time.sleep(float(config['enverproxy']['delay']))
            ss = select.select
            inputready, outputready, exceptready = ss(self.input_list, [], [])
            if config['enverproxy']['DEBUG']:
                self.__log.logMsg('Inputready: ' + str(inputready))
            for self.s in inputready:
                if self.s == self.server:
                    # proxy server has new connection request
                    self.on_accept()
                    break
                # get the data
                try:
                    self.data = self.s.recv(int(config['enverproxy']['buffer_size']))
                    if config['enverproxy']['DEBUG']:
                        self.__log.logMsg('Main loop: ' + str(len(self.data)) + ' bytes received from ' + str(self.s.getpeername()))
                    if not self.data or len(self.data) == 0:
                        # Client closed the connection
                        self.on_close()
                        break
                    else:
                        self.on_recv()
                except OSError as e:
                    self.__log.logMsg('Main loop socket error: ' + str(e))
                    time.sleep(1) 
                    if e.errno in (errno.ENOTCONN, errno.ECONNRESET):
                        # Connection was closed abnormally
                        self.on_close()
                else:
                    continue

    def on_accept(self):
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg('Entering on_accept')
        forward = Forward(self.__log).start(config['enverproxy']['forward_IP'], int(config['enverproxy']['forward_port']))
        clientsock, clientaddr = self.server.accept()
        if forward:
            self.__log.logMsg(str(clientaddr) + ' has connected')
            self.input_list.append(clientsock)
            self.input_list.append(forward)
            if config['enverproxy']['DEBUG']:
                self.__log.logMsg('New connection list: ' + str(self.input_list))
            self.channel[clientsock] = forward
            self.channel[forward] = clientsock
            if config['enverproxy']['DEBUG']:
                self.__log.logMsg('New channel dictionary: ' + str(self.channel))
        else:
            self.__log.logMsg("Can't establish connection with remote server.")
            self.__log.logMsg("Closing connection with client side" + str(clientaddr))
            clientsock.close()

    def on_close(self):
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg('Entering on_close')
        in_s  = self.s
        out_s = self.channel[self.s]
        try:
            # close the connection with client
            self.__log.logMsg(str(in_s.getpeername()) + " has disconnected")
            in_s.close()
        except OSError as e:
            self.__log.logMsg('On_close socket error with ' + str(in_s) + ': ' + str(e))
        try:
            # close the connection with remote server
            out_s.close()
        except OSError as e:
            self.__log.logMsg('On_close socket error with ' + str(out_s) + ': ' + str(e))
        #remove objects from input_list
        self.input_list.remove(in_s)
        self.input_list.remove(out_s)
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg('Remaining connection list: ' + str(self.input_list))
        # delete both objects from channel dict
        del self.channel[in_s]
        del self.channel[out_s]
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg('Remaining channel dictionary: ' + str(self.channel))

    def extract(self, data, wrind):
        pos1 = 40 + (wrind*64)
        # Extract information from bytearray
        #               1        2                    4        4    5    5    6        6    7    7 
        # 0      6      2        0                    0        8    2    6    0        8    2    6
        # -------------------------------------------------------------------------------------------
        # cmd    cmd    account                       wrid     ?    dc   pwr  totalkWh temp ac   F
        # -------------------------------------------------------------------------------------------
        # 6803d6 681004 yyyyyyyy 00000000000000000000 xxxxxxxx 2202 40d0 352b 001c5f39 1d66 3872 3204
        #
        d_wr_id         = data[pos1:pos1+8]
        d_hex_dc        = data[pos1+12:pos1+12+4]
        d_hex_power     = data[pos1+16:pos1+16+4]
        d_hex_total     = data[pos1+20:pos1+20+8]
        d_hex_temp      = data[pos1+28:pos1+28+4]
        d_hex_ac        = data[pos1+32:pos1+32+4]
        d_hex_freq      = data[pos1+36:pos1+36+4]
        d_hex_remaining = data[pos1+40:pos1+40+24]
        # Calculation
        d_dez_dc    = '{0:.2f}'.format(int(d_hex_dc, 16)/512)
        d_dez_power = '{0:.2f}'.format(int(d_hex_power, 16)/64)
        d_dez_total = '{0:.2f}'.format(int(d_hex_total, 16)/8192)
        d_dez_temp  = '{0:.2f}'.format(((int(d_hex_temp[0:2], 16)*256+int(d_hex_temp[2:4], 16))/ 128)-40)
        d_dez_ac    = '{0:.2f}'.format(int(d_hex_ac, 16)/64)
        d_dez_freq  = '{0:.2f}'.format(int(d_hex_freq[0:2], 16)+int(d_hex_freq[2:4], 16)/ 256)
        # Ignore if converter id is zero
        if int(d_wr_id) != 0:
            result = {'wrid' : d_wr_id, 'dc' : d_dez_dc, 'power' : d_dez_power, 'totalkwh' : d_dez_total, 'temp' : d_dez_temp, 'ac' : d_dez_ac, 'freq' : d_dez_freq, 'remaining' : d_hex_remaining}
            return result

    def submit_data(self, wrdata):
        # Can be https as well. Also: if you use another port then 80 or 443 do not forget to add the port number.
        # user and password.
        url       = 'https://' + config['enverproxy']['host'] + ':8083/fhem?'
        user      = config['enverproxy']['user']
        password  = config['enverproxy']['password']
        ID2device = ast.literal_eval(config['enverproxy']['id2device'])
        fhem_server = FHEM(url, user, password, self.__log)
        for wrdict in wrdata:
            if config['enverproxy']['DEBUG']:
                self.__log.logMsg('Submitting data for converter: ' + str(wrdict['wrid']) + ' to FHEM')
            values = 'wrid', 'ac', 'dc', 'temp', 'power', 'totalkwh', 'freq'
            for value in values:
                if wrdict['wrid'] in ID2device:
                    fhem_cmd = 'set ' + ID2device[wrdict['wrid']] + ' ' + value + ' ' + wrdict[value]
                    fhem_server.send_command(fhem_cmd)
                    if config['enverproxy']['DEBUG']:
                        self.__log.logMsg('FHEM command: ' + fhem_cmd)
                else:
                    self.__log.logMsg('No FHEM device known for converter ID ' + wrdict['wrid'])
        self.__log.logMsg('Data submitted to FHEM')

    def process_data(self, data):
        datainhex = data.hex()
        wr = []
        wr_index = 0
        wr_index_max = 20
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg("Processing Data")
        while True:
            response = self.extract(datainhex, wr_index)
            if response:
                if config['enverproxy']['DEBUG']:
                    self.__log.logMsg('Decoded data from microconverter with ID ' + str(response['wrid']))
                wr.append(response)
            wr_index += 1
            if wr_index >= wr_index_max:
                break
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg('Finished processing data for ' + str(len(wr)) + ' microconverter: ' + str(wr))
        else:
            self.__log.logMsg('Processed data for ' + str(len(wr)) + ' microconverter')
        self.submit_data(wr)

    def handshake(self, data):
        data = bytearray(data)
        # Microconverter starts with 680030681006
        if data[:6].hex() == '680030681006':
            # microconverter expects reply starting with 680030681007
            data[5] = 7
            return data
        else:
            self.__log.logMsg('Microconverter sent wrong start sequence ' + str(data[:6].hex()))

    def on_recv(self):
        data = self.data
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg(str(len(data)) + ' bytes in on_recv')
            self.__log.logMsg('Data as hex: ' + str(data.hex()))
        if self.s.getsockname()[1] == int(config['enverproxy']['listen_port']):
            # receving data from a client
            if config['enverproxy']['DEBUG']:
                self.__log.logMsg('Data is coming from a client')
            if (len(data) == 48) and (data[:6].hex() == '680030681006'):
                # converter initiates connection
                # create reply packet
                reply = self.handshake(data)
                """
                # This part is simulating handshake with envertecportal.com
                # Keep disabled if working as proxy between Enverbridge and envertecportal.com
                if config['enverproxy']['DEBUG']:
                    self.__log.logMsg('Replying to handshake with data ' + str(reply.hex()))
                self.s.send(reply)
                if config['enverproxy']['DEBUG']:
                    self.__log.logMsg('Reply sent to: ' + str(self.s))
                """
            elif (len(data) == 982) and (data[:6].hex() == '6803d6681004'):
                # payload from converter
                self.process_data(data)
            else:
                self.__log.logMsg('Client sent message with unknown content and length ' + str(len(data)))
        # forward data to proxy pair
        self.channel[self.s].send(data)
        if config['enverproxy']['DEBUG']:
            self.__log.logMsg('Data forwarded to: ' + str(self.channel[self.s]))


if __name__ == '__main__':
        l = slog('Envertec Proxy')
        l.logMsg('Starting server (v' + str(config['internal']['version']) + ')')
        if os.path.isfile(config['internal']['conf_file']):
           config.read(config['internal']['conf_file'])
           if 'enverproxy' not in config:
               l.logMsg('Section [enverproxy] is missing in config file ' + config['internal']['conf_file'])
               l.logMsg('Stopping server')
               sys.exit(1)
           for k in ast.literal_eval(config['internal']['keys']):
               if k not in config['enverproxy']:
                   l.logMsg('Config variable "' + k + '" is missing in config file ' + config['internal']['conf_file'])
                   l.logMsg('Stopping server')
                   sys.exit(1)
        else:
            l.logMsg('Configuration file ' + config['internal']['conf_file'] + ' not found')
            l.logMsg('Stopping server')
            sys.exit(1)
        server = TheServer('', int(config['enverproxy']['listen_port']), l)
        try:
            server.main_loop()
        except KeyboardInterrupt:
            l.logMsg("Ctrl C - Stopping server")
            sys.exit(1)
