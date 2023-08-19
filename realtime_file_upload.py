
# TODO
#
# make a wrapper around `con.recv` that is checks for the time it has taken the client to send the data
#
# use FREQ

import network
import time
from machine import Pin
import socket
import ssl
#import _thread
import uasyncio

# defines

WIFI_SSID = 'your ssid here'
WIFI_PASS = 'your pass here'

BIND_PORT = 80

KEYFILE = 'certificates/private.key'
CERTFILE = 'certificates/selfsigned.crt'
#SSL_VERSION = ssl.PROTOCOL_SSLv23

HEADER_MAXLEN = 1024 * 5
FILE_UPLOAD_CHUNK = 1024
FILE_UPLOAD_MAX_CHUNKS = 5
SHARED_DATA_FILE_CONTENT_MAX_LEN = 5

ENCODING = 'utf-8'

PAGE_FILE_UPLOAD = '/file-upload'
PAGE_FILE_DOWNLOAD = '/file-download'
PAGE_FILE_UPLOAD_IN_PROGRESS = '/file-upload-in-progress'

WAIT_FOR_NEW_CONNECTION_SLEEP = 0.3
WAIT_FOR_FILE_DOWNLOAD_TO_START_SLEEP = 0.5
WAIT_FOR_PIECES_FROM_UPLOADER_SLEEP = 0.2
RECV_HEADER_1BYTE_SLEEP = 0.01

RECV_HEADER_1BYTE_TIMEOUT = 0.5

# globals (unfortunate)

led = Pin("LED", Pin.OUT)

# classes

class Shared_data:
    file_upload_is_being_requested = False
    file_download_in_progress = False

    file_content = []
    file_name = 'ERROR'

# functions

def connect_to_wifi(ssid, password):
    print(f'trying to connect to wifi `{ssid}`')
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    while wlan.isconnected() == False:
        print('connecting...')
        led.toggle()
        time.sleep(1)
    print('connected')
    led.on()
    
    ip, subnet, gateway, dns = wlan.ifconfig()
    print(f'assigned ip `{ip}`')
    
async def receive_header_line(con):
    end = b'\r\n'
    
    last_byte_received = time.time()
    data = b''
    while not data.endswith(end):
        try:
            data += con.recv(1)
        except OSError:
            if time.time() - last_byte_received > RECV_HEADER_1BYTE_TIMEOUT:
                break
            await uasyncio.sleep(RECV_HEADER_1BYTE_SLEEP)
        else:
            last_byte_received = time.time()
    return data[:-len(end)].decode()

def page_file_upload(con):
    con.sendall(f'''<!DOCTYPE html>
<html>
<form action="{PAGE_FILE_UPLOAD_IN_PROGRESS}" method="post" enctype="multipart/form-data">
  <label for="file">File</label>
  <input id="file" name="file" type="file" />
  <button>Upload</button>
</form>
</html>
'''.encode(ENCODING))

async def _page_file_download(con, shared):
    file_name = shared.file_name
    if '"' in file_name:
        print('ERROR: bad char in file name')
        file_name = file_name.replace('"', '_')
    
    print(f'staring download of file `{file_name}`')
    
    con.sendall(f'''HTTP/1.1 200 OK
Content-disposition: attachment; filename="{file_name}"

'''.encode(ENCODING))
    
    while shared.file_upload_is_being_requested:
        while len(shared.file_content) > 0:
            data = shared.file_content.pop(0)
            con.settimeout(None)
            con.sendall(data)
            con.settimeout(0)
        else:
            await uasyncio.sleep(WAIT_FOR_PIECES_FROM_UPLOADER_SLEEP)

async def page_file_download(con, shared):
    if not shared.file_upload_is_being_requested:
        con.sendall(b'no one is uploading a file')
        print('no one is uploading a file')
        return

    if shared.file_download_in_progress:
        con.sendall(b'someone is already downloading')
        print('someone is already downloading')
        return

    print('well do a download')

    shared.file_download_in_progress = True
    try:
        await _page_file_download(con, shared)
    finally:
        shared.file_download_in_progress = False

def page_main(con):
    con.sendall(f'''<!DOCTYPE html>
<html>
  <head>
    <title>Redirecting without leaving the current page</title>
  </head>
  <body>
    <a href="{PAGE_FILE_UPLOAD}" target="_blank">file upload</a>
    <br>
    <a href="{PAGE_FILE_DOWNLOAD}" target="_blank">file download</a>
  </body>
</html>
'''.encode(ENCODING))

async def _page_file_upload_in_progress(con, shared):
    # this sucks but Ican't figure out anything else
    # maybe that's why there is a lagre random number here
    # (if this really is the case, then the people who made this are idiots)
    ending = '\r\n' + (await receive_header_line(con)) + '--\r\n'
    ending = ending.encode(ENCODING)
    #print(f'++++ {ending=}')
    
    file_name = None
    line = 0
    while True:
        line += 1
        data = (await receive_header_line(con))
        print(data)
        
        if data.startswith('Content-Disposition:'):
            data = data.split('; ')
            for dat in data:
                tmp = 'filename='
                if dat.startswith(tmp):
                    file_name = dat[len(tmp):]
                    break
        elif len(data) == 0:
            break

    if file_name == None:
        con.sendall(b'ERROR: could not determine file name')
        return
    
    if file_name.startswith('"') and file_name.endswith('"'):
        if len(file_name) >= 2:
            file_name = file_name[1:-1]
    
    shared.file_name = file_name
    
    while not shared.file_download_in_progress:
        print('waiting for file download to start...')
        await uasyncio.sleep(WAIT_FOR_FILE_DOWNLOAD_TO_START_SLEEP)
    
    data = [b''] * FILE_UPLOAD_MAX_CHUNKS
    length = 0
    while True:
        chunk = con.recv(FILE_UPLOAD_CHUNK)
        #if len(chunk) == 0: # emergency exit, ideally would be replaced
        #    break
        print('received chunk')
        #print(chunk)
        length += len(chunk)
        #print(f'size is {length}')
        # data += chunk
        
        data.append(chunk)
        if (data[-2] + data[-1]).endswith(ending):
            del data[-1]
            del data[-1]
            break
        
        while len(shared.file_content) >= SHARED_DATA_FILE_CONTENT_MAX_LEN:
            # this might cause an infinite loop if the downloader disconnects
            await uasyncio.sleep(0.5)
        shared.file_content.append(data[0])
        del data[0]
        # this really should be send the data to the sending thread
    
    # this ignores the limits
    while len(data) > 0:
        shared.file_content.append(data[0])
        del data[0]
    
    while len(shared.file_content) > 0:
        await uasyncio.sleep(0.5)
    
    print(f'file uploaded complete')
    con.sendall(b'file upload complete')

async def page_file_upload_in_progress(con, shared):
    if shared.file_upload_is_being_requested:
        con.sendall('someone is already uploading a file; wait for him to finish')
        return

    shared.file_upload_is_being_requested = True
    try:
        await _page_file_upload_in_progress(con, shared)
    finally:
        shared.file_upload_is_being_requested = False

async def accept_next_connection(sock, shared):
    print('waiting for connection')
    
    while True:
        try:
            con, addr = sock.accept()
        except OSError:
            await uasyncio.sleep(WAIT_FOR_NEW_CONNECTION_SLEEP)
        else:
            break
    
    print(f'accepted connection from {addr}')
    
    important = (await receive_header_line(con))
    try:
        method, page, http = important.split(' ')
    except ValueError:
        # invalid header
        return
    
    while True:
        data = (await receive_header_line(con))
        if len(data) == 0:
            break
        #if 'Content-Length' in data:
        #    print(f'+++ cool header line: {data}')
    
    print(f'~~~~~~ {page=}')
    if page == PAGE_FILE_UPLOAD:
        page_file_upload(con)
    elif page == PAGE_FILE_DOWNLOAD:
        await page_file_download(con, shared)
    elif page == PAGE_FILE_UPLOAD_IN_PROGRESS:
        await page_file_upload_in_progress(con, shared)
    else:
        page_main(con)
    
    con.close()
    print(f'~~~~~~ request satisfied')

def continious_client_server(sock, shared):
    while True:
        await accept_next_connection(sock, shared)

async def _main(sock):
    shared = Shared_data()

    #_thread.start_new_thread(continious_client_server, (sock, shared))

    uasyncio.create_task(continious_client_server(sock, shared))
    uasyncio.create_task(continious_client_server(sock, shared))
    
    while True:
        await uasyncio.sleep(100_000)

def main():
    # indicate that the program has started
    led.off()
    time.sleep(0.1)
    led.on()
    time.sleep(0.1)
    led.off()
    time.sleep(0.1)
    led.on()

    connect_to_wifi(WIFI_SSID, WIFI_PASS)

    sock = socket.socket()

    #sock = ssl.wrap_socket(
        #sock,
        #keyfile=KEYFILE,
        #certfile=CERTFILE,
        #server_side=True,
        #ssl_version=SSL_VERSION,
        #do_handshake_on_connect=True
    #)

    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    print(f'trying to bind to port {BIND_PORT}')
    sock.bind(('', BIND_PORT))
    print('done')
    sock.listen(1)
    sock.settimeout(0) # non-blocking
    
    try:
        uasyncio.run(_main(sock))
    except KeyboardInterrupt:
        pass
    
    sock.close()

main()

