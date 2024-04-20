# ws_client_audio_2.py

import sys
import os
import time
from collections import deque
import json
import asyncio
# do not use statement <import websockets> but do this...; then code completion will work
# this hint was found here: https://github.com/python-websockets/websockets/issues/1183
import websockets.client
import websockets.exceptions

async def clientConnect(uri):
    # try to connect to websocket server

    try:
        websocket = await websockets.client.connect(uri, close_timeout=None)
    except websockets.exceptions.ConnectionClosed as ex:
        print("could not connect -> exception: {ex}")
        return None
    # if successful -> return websocket object
    return websocket
    
async def collectEvents(dequeEvents: deque, dequeAudioFiles: deque, websocket: websockets.client.WebSocketClientProtocol, recordings_dir: str):
    # listen for notifications from server and echo back ...
    while True:
        try:
            response = await websocket.recv()
            response_D = json.loads(response)
            response_type = response_D["event_id"]
            print(f"response_D: {response_D}\n")
            
            # put into queue depending on response_type
            if response_type == "soundActivity":
                print(f"soundActivity detected")
                dequeEvents.append(response_D)
            elif response_type == "audioFileCreated":
                print(f"audio file has been created by server application")
                dequeAudioFiles.append(response_D)
            elif response_type == "audioFileSent":
                t_start = time.perf_counter()
                audioData  = await websocket.recv()
                t_elapsed = time.perf_counter() - t_start
                print(f"nr of bytes of audio data received: {len(audioData)} after: {t_elapsed:10.3f} seconds")
                # full path name of downloaded audio file
                audioFileName = os.path.join(recordings_dir, os.path.basename(dequeAudioFiles[-1]['audio_file']))

                with open(audioFileName, 'wb') as fid:
                    fid.write(audioData)
                    print(f"audio file downloaded: {audioFileName}")
            else:
                print(f"event_id: {response_type} -> not supported")
                
            await asyncio.sleep(0)
        except websockets.exceptions.ConnectionClosed as ex:
            print(f"connection closed -> exit client program")
            print(f"ex: {ex}")
            break        
        
async def main(configDict, uri):
    
    basedir = os.path.dirname(os.path.dirname(__file__))
    
    enable_downloads = configDict['enable_downloads']
    # extend the relative path to an absolute pathe
    recordings_dir = os.path.join(basedir, configDict['recordings_dir'])
    if not os.path.exists(recordings_dir):
        sys.exit(f"directory: {recordings_dir}\ndoes not exist")
    
    websocket = await clientConnect(uri)
    
    if websocket is None:
        print("no connection established -> exiting")
        return
    
    if enable_downloads:
        msg_D = {'event_id': 'downloadEnable', 'value': enable_downloads}
        await websocket.send(json.dumps(msg_D))
        response = await websocket.recv()
        response_D = json.loads(response)
    
        if response_D['event_id'] != 'downloadEnable' and response_D['value']:
            sys.exit(f"enabling download of audio files failed -> exit program")
        
    # initialisations
    eventAudioFile = asyncio.Event()
    dequeEvents = deque(maxlen= configDict["len_recent_events"])
    dequeAudioFiles = deque(maxlen= configDict["len_recent_events"])
    
    coro1 = collectEvents(dequeEvents, dequeAudioFiles, websocket, recordings_dir)
    result = await asyncio.gather(coro1)
        
    return result
        

if __name__ == "__main__":
    from argparse import ArgumentParser
    
    parser = ArgumentParser()
    parser.add_argument("config_JS", help="configuration file (*.json)")
    parser.add_argument("uri", help="uri ; example: 'ws://192.168.0.193:8765' ")
    args = parser.parse_args()
    
    with open(args.config_JS, 'r') as fid:
        configDict  = json.load(fid)
    
    asyncio.run(main(configDict, args.uri))
    print(f"client -> exiting")
        