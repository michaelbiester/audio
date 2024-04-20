# ws_server_audio_2.py

"""
an audio application using asyncio
 
"""

import asyncio
# do not import websockets but do this...; then code completion will work
# this hint was found here: https://github.com/python-websockets/websockets/issues/1183

import sys
import os
import time
from functools import partial
import queue
from collections import deque
import numpy as np
import soundfile as sf
import sounddevice as sd
import websockets.server
import websockets.exceptions


# the callback (uses numpy interally)
# -> the main purpose of the callback is to transfer audio samples to a queue
#    any further processing is done outside the callback function
def callback_recorder(audioQueue, indata, frames, time, status):
    if status:
        print(status)
    audioQueue.put(indata.copy())

async def respondToClient(websocket: websockets.server.WebSocketServerProtocol, downloadAudioEvent: asyncio.Event):
    """_summary_

    Args:
        websocket (_type_): _description_
        
    responds to request send by client
    """
    while True:
        try:
            response = await websocket.recv()
            await asyncio.sleep(0)
            try:
                response_D = json.loads(response)
                response_type = response_D["event_id"]
                print(f"response_D: {response_D}\n")
            except:
                response_type = None
                print(f"response_D: not a dictionary\n")
                continue
                
            # actions depending on response type
            if response_type == 'downloadEnable':
                enable_downloads = response_D['value']
                
                if enable_downloads:
                    downloadAudioEvent.set()
                else:
                    downloadAudioEvent.clear()
                
                # notify client that download of audio files has been enabled / disabled
                msg_D = {'event_id': 'downloadEnable', 'value': enable_downloads}
                await websocket.send(json.dumps(msg_D))      
        except websockets.exceptions.ConnectionClosed as ex:
            print(f"connection closed -> reason: {ex}")
            # finish task / coroutine
            break

async def collectAudioData(configDict, notifyEvent: asyncio.Event, downloadAudioEvent: asyncio.Event, audioQueue: queue.Queue, msgQueue: asyncio.Queue, dataQueue: queue.Queue):
    # initialise
    try:
        print("processing configuration")
        device_index = configDict["device_index"]
        nr_channels = configDict["channels"]
        samplerate_hz = int(configDict["samplerate_hz"])
        buffer_duration_s = configDict["buffer_duration_s"]
        nr_buffers = configDict["nr_buffers"]
        nr_records_to_file = configDict["nr_records_to_file"]
        chunk_mod = configDict["chunk_mod"]

        # sound event & audio file related info
        activity_threshold = configDict["activity_threshold"]
        nr_cycles = configDict["nr_cycles"]
        len_recent_events = configDict["len_recent_events"]
        dequeEvents = deque(maxlen=len_recent_events)
        dequeAudioFiles = deque(maxlen=len_recent_events) 
        
        # split file name
        base_wav, wav_ext = os.path.splitext(configDict["out_audio_file_wav"])
    except:
        sys.exit('invalid configuration')
        
    # each buffer shall have these number of samples 
    nr_samples_buf = int(buffer_duration_s * samplerate_hz)

    # preallocation of memory (slightly larger than required)
    audio_buffers = [np.zeros(nr_samples_buf, dtype=np.float32) for k in range(nr_buffers)]
    # samples in each buffer are stored in this array
    nr_samples_buffer = np.zeros(nr_buffers, dtype=np.uint32)

    # initialisation: total number of buffers collected so far ...    
    soundEventList = []
    nr_runs = 0
    
    # count the number of callback invocations used in inputStream
    # after every chuck_mod number of callbacks the event loop is 
    # activated using await asyncio.sleep(0)
    chunk_count = 0
    
    do_soundprocessing = True
    inpStream = None
 
    # wrap the callback -> the wrapped function has the signature <indata, frames, time, status> 
    wrapped_callback = partial(callback_recorder, audioQueue)
    
    # outer while loop
    while do_soundprocessing:
        print(f"listening for sound activity -> opening inputStream")

        # initialisations for buffers
        idx = 0
        count_samples = 0
        buffer_id = 0
        nr_samples_buffer[:] = 0
    
        inpStream = sd.InputStream(samplerate=samplerate_hz, device=device_index, channels=nr_channels, callback=wrapped_callback)
        inpStream.start()

        # initialise flags
        sound_activity = False 
        collection_audio = True

        # data collection stage -> inner while loop
        while collection_audio:
            # get chunk of audio data
            # and determine nr of data (sounddevice recommends not to specifiy the number of data explicitely)
            data = audioQueue.get()
            audioQueue.task_done()
            ndata = len(data)
            # print(ndata)

            count_samples += ndata
            # determine into which buffer audio data stored        
            if count_samples >= nr_samples_buf:
                # start recording into the next buffer
                count_samples = ndata
                # reset insertion point to start writing from the start of the next buffer
                idx = 0
                # id of next buffer (modulo)
                buffer_id = (buffer_id + 1) % nr_buffers
                # update number of buffers which have been already filled
                print(f"nr_runs: {nr_runs}")
                nr_runs += 1
                
            # are we done with collecting audio samples ?
            if nr_runs >= nr_cycles:
                do_soundprocessing = False
                collection_audio = False
                inpStream.stop()
                inpStream.close()
                # get out of inner while loop and then out of outer loop
                # getting out of the outer loop finishes the coroutine
                break
            
            # keep event loop responsive ...
            chunk_count += 1
            if chunk_count % chunk_mod == 0:
                await asyncio.sleep(0)

            # update current audio buffer -> insert data
            audio_buffers[buffer_id][idx:idx + ndata] = data[:,0]
            # update insertion point
            idx = idx + ndata
            # update number of audio samples in current audio buffer
            nr_samples_buffer[buffer_id] = idx
            
            # a simple sound activity detector
            if not sound_activity:
                sound_activity_score  = float(np.sum(np.abs(data)))
                # print(f"sound_activity_score: {sound_activity_score}")
                if sound_activity_score >= activity_threshold:
                    sound_activity = True
                    # capture audio data into a file
                    # start with the current buffer -> buffer_id_start
                    # finish with buffer -> buffer_id_stop
                    buffer_id_start = buffer_id
                    buffer_id_stop = (buffer_id + nr_records_to_file - 1) %  nr_buffers 

                    # the name of the sound file
                    file_wav = base_wav + f"_run_{nr_runs}" + wav_ext
                    event_nr_runs = nr_runs
                    activity_D = {"event_id": "soundActivity", "activity_score": sound_activity_score, "activity_threshold": activity_threshold, 
                                  "buffer_id_start": buffer_id_start, "insertion point": idx, "nr_runs": event_nr_runs}
                    soundEventList.append(activity_D)
                    dequeEvents.append(activity_D)
                    await msgQueue.put(activity_D)
                    print(f"sound activity: {activity_D}")
                    notifyEvent.set()
                    await asyncio.sleep(0)
                    
            # write current buffer to file
            if sound_activity and (buffer_id == buffer_id_stop):
                inpStream.stop()
                inpStream.close()

                # stopping the input stream may still result in residual entries in the queue
                # for a clean restart of collecting audio samples these entries will be removed
                # until the size of the queue drops to zero (this has been found necessary on a Raspberry PI 3)
                
                qSize_before = audioQueue.qsize()
                qSize_current = qSize_before
                while qSize_current > 0:
                    # get junk data 
                    audioQueue.get()
                    qSize_current = audioQueue.qsize()
                
                print(f"audioQueue.qsize() after stopping stream: {qSize_before}")
                print(f"audioQueue.qsize() after stopping stream and reading items from queue: {qSize_current}")

                # open soundfile 
                sfi = sf.SoundFile(file_wav, mode='w', samplerate=samplerate_hz, channels=nr_channels)
                selected_buffer = buffer_id_start
                for k in range(nr_records_to_file):
                    sfi.write(audio_buffers[selected_buffer][:nr_samples_buffer[selected_buffer]])
                    selected_buffer = (selected_buffer + 1) % nr_buffers      

                # close audio file
                sfi.close()
                collection_audio = False
                msgAudioFile_D = {"event_id": "audioFileCreated", "nr_runs": event_nr_runs, "audio_file": file_wav}
                dequeAudioFiles.append(msgAudioFile_D)
                
                await msgQueue.put(msgAudioFile_D)
                print(f"created audio file: {msgAudioFile_D}")
                notifyEvent.set()
                await asyncio.sleep(0)   
                
                # shall the audio file be sent to the client ?
                if downloadAudioEvent.is_set():
                    with open(file_wav, 'rb') as fid:
                        buf = fid.read(-1)
                        dataQueue.put(buf)
                        dataQueue.task_done()
                    
                    msgAudioFileSent_D= {"event_id": "audioFileSent", "audio_file": file_wav}
                    await msgQueue.put(msgAudioFileSent_D)
                    notifyEvent.set()
                    await asyncio.sleep(0)   
        
async def sendNotification(websocket: websockets.server.WebSocketServerProtocol, notifyEvent: asyncio.Event, msgQueue: asyncio.Queue, dataQueue: queue.Queue):
    """_summary_
    Args:
        websocket (_type_): _description_
        notifyEvent (asyncio.Event): _description_
        msgQueue (asyncio.Queue): _description_
        
        the queue msdQueue contain dictionary type objects.
    """
    # run in infinite loop
    while True:
        await notifyEvent.wait()
        # print(f"event is set -> send notification") 
        msg_D = await msgQueue.get()
        event_id = msg_D['event_id']
        msgQueue.task_done()
        msg_str = json.dumps(msg_D)

        try:
            await websocket.send(msg_str)
            notifyEvent.clear()
            await asyncio.sleep(0)
            
            # sent stuff to client depending on type of event
            # currently only this event triggers sending data to client
            if event_id == 'audioFileSent':
                buf = dataQueue.get()
                print(f"audio file will be sent; nr of bytes: {len(buf)}")
                # dataQueue.task_done()
                t_start = time.perf_counter()
                await websocket.send(buf)
                t_elapsed = time.perf_counter() - t_start
                print(f"sending took: {t_elapsed:10.3f} seconds")
                await asyncio.sleep(0.0)
            # add processing of other events if necessary ...
            else:
                pass
        except websockets.exceptions.ConnectionClosed:
            print("connection has been closed -> stop sending notification to client")
            break
           
async def wsHandler(configDict, websocket: websockets.server.WebSocketServerProtocol):
    """_summary_
    
    the handler function for the websocket server
    
    note: this handler cannot be passed directly to the server function
    it must be wrapped using functools.partial to have the required number & positions of function
    parameters.
    """
    msgQueue = asyncio.Queue()  
    audioQueue = queue.Queue()
    dataQueue = queue.Queue()
      
    remote_address = websocket.remote_address
    print(f"remote address (client) : {remote_address}")
    
    notifyEvent = asyncio.Event()
    downloadAudioEvent = asyncio.Event()
    
    co_collectAudioData = collectAudioData(configDict, notifyEvent, downloadAudioEvent, audioQueue, msgQueue, dataQueue)
    co_sendNotification = sendNotification(websocket, notifyEvent, msgQueue, dataQueue)
    co_respondToClient = respondToClient(websocket, downloadAudioEvent)
    
    result = await asyncio.gather(co_collectAudioData, co_sendNotification, co_respondToClient)
    print(f"result: {result}")
    
async def main(configDict, host, ws_port):
    wrapped_wsHandler = partial(wsHandler, configDict)
    
    async with websockets.server.serve(wrapped_wsHandler, host, ws_port, close_timeout=None):
        await asyncio.Future()
        
#-----------------------------------------

if __name__ == "__main__":
    
    import json
    from argparse import ArgumentParser
    
    parser = ArgumentParser()
    parser.add_argument("config_JS", help="configuration file of handler (*.json)")
    args = parser.parse_args()
    
    with open(args.config_JS, 'r') as fid:
        configDict = json.load(fid)    
    # connection parameters
    host = configDict["host"]
    ws_port = configDict["ws_port"]

    # run the server
    asyncio.run(main(configDict, host, ws_port))