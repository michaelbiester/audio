# audio_recording_3b.py

"""
recording audio samples and saving into an audio file

a number of audio buffers is preallocated and then used in 
a circular fashion:

1) collect audio samples in buffer Nr.1
2) repeat with buffer Nr. 2 etc.
3) after having written data to the last buffer , repeat the procedure starting with buffer Nr. 1

A maximum number (configurable) of write cycle is used. The program exits after this number of write cycles
has been performed

The main difference with program audio_recording_3a.py

1) if sound activity has been detected the required number of audio buffers are collected.

2) the collection of sound data is stopped and the buffers are saved into an audio file

3) the collection of sound data is restarted. Hopefully stopping sound data while saving to audio file reduces the computational
load. It may not matter much on a PC however it should have some effect with platforms with restricted processing capabilities (eg.
Raspberry Pi)
"""

import soundfile as sf
import sounddevice as sd
import numpy as np
import queue
from functools import partial
import time

# the callback (uses numpy interally)
# -> the main purpose of the callback is to transfer audio samples to a queue
#    any further processing is done outside the callback function
def callback_ref(q, indata, frames, time, status):
    if status:
        print(status)
    q.put(indata.copy())


if __name__ == "__main__":

    from argparse import ArgumentParser
    import json
    import sys
    import os

    parser = ArgumentParser()
    parser.add_argument('configJS', help="configuration file (json)")
    parser.add_argument('nr_cycles', type=int, help="maximum nr of recording cycles")
    parser.add_argument('sound_activity_threshold', type=float, help="sound activity threshold")
    parser.add_argument('outAudioWav', help="audio output file (*.wav)")
    parser.add_argument('soundEvent_JS', help="sound events are stored into this file (*.json)")

    args = parser.parse_args()

    with open(args.configJS, mode='r') as cfg:
        config_D = json.load(cfg)

        try:
            print("processing configuration")
            device_index = config_D["device_index"]
            nr_channels = config_D["channels"]
            samplerate_hz = int(config_D["samplerate_hz"])
            buffer_duration_s = config_D["buffer_duration_s"]
            nr_buffers = config_D["nr_buffers"]
            nr_records = config_D["nr_records"]

            if nr_records > nr_buffers:
                sys.exit(f"nr_records {nr_records} exceeds nr_buffers {nr_buffers}")

            sound_activity_threshold = args.sound_activity_threshold
            nr_cycles = args.nr_cycles

            base_wav, wav_ext = os.path.splitext(args.outAudioWav)
        except:
            sys.exit('invalid configuration')

    # a queue into which the callback function stores audio samples
    q = queue.Queue()
 
    # wrap the callback -> the wrapped function has the signature <indata, frames, time, status> 
    wrapped_callback = partial(callback_ref, q)

    # each buffer shall have these number of samples 
    nr_samples_buf = int(buffer_duration_s * samplerate_hz)

    # preallocation of memory (slightly larger than required)
    audio_buffers = [np.zeros(nr_samples_buf, dtype=np.float32) for k in range(nr_buffers)]
    # samples in each buffer are stored in this array
    nr_samples_buffer = np.zeros(nr_buffers, dtype=np.uint32)

    # initialisation: total number of buffers collected so far ...
    soundEventList = []
    nr_runs = 0
    do_soundprocessing = True

    inpStream = None

    # outer while loop
    while do_soundprocessing:
        
        print(f"listening for sound activity -> opening inputStream")

        # initialisations for buffers
        idx = 0
        count_samples = 0
        buffer_id = 0
        nr_samples_buffer[:] = 0
        # stop_inputStream = False

        inpStream = sd.InputStream(samplerate=samplerate_hz, device=1, channels=nr_channels, callback=wrapped_callback)
        inpStream.start()

        sound_activity = False 
        collection_audio = True

        # data collection stage -> inner while loop
        while collection_audio:
            # get chunk of audio data
            # and determine nr of data (sounddevice recommends not to specifiy the number of data explicitely)
            data = q.get()
            q.task_done()
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

            if nr_runs >= nr_cycles:
                do_soundprocessing = False
                collection_audio = False
                inpStream.stop()
                inpStream.close()
                # get out of inner while loop and then out of outer loop
                break

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
                if sound_activity_score >= sound_activity_threshold:
                    sound_activity = True
                    # capture audio data into a file
                    # start with the current buffer -> buffer_id_start
                    # end finish with buffer -> buffer_id_stop
                    buffer_id_start = buffer_id
                    buffer_id_stop = (buffer_id + nr_records - 1) %  nr_buffers 

                    # the name of the sound file
                    file_wav = base_wav + f"_run_{nr_runs}" + wav_ext
                    activity_D = {"activity_score": sound_activity_score, "activity_threshold": sound_activity_threshold, 
                                  "buffer_id_start": buffer_id_start, "insertion point": idx, "nr_runs": nr_runs, "audio_file": file_wav}
                    soundEventList.append(activity_D)
                    print(f"sound activty detected -> activity data {activity_D}")


            # write current buffer to file
            if sound_activity and (buffer_id == buffer_id_stop):
                inpStream.stop()
                inpStream.close()
                print(f"q.qsize() after stopping stream: {q.qsize()}")

                # stopping the input stream may still result in residual entries in the queue
                # for a clean restart of collecting audio samples these entries will be remove
                # until the size of the queue drops to zero (this has been found necessary on a Raspberry PI 3)
                while q.qsize() > 0:
                    dtmp = q.get()
                
                print(f"q.qsize() after stopping stream and reading items from queue: {q.qsize()}")

                # open soundfile 
                sfi = sf.SoundFile(file_wav, mode='w', samplerate=samplerate_hz, channels=nr_channels)
                selected_buffer = buffer_id_start
                for k in range(nr_records):
                    sfi.write(audio_buffers[selected_buffer][:nr_samples_buffer[selected_buffer]])
                    selected_buffer = (selected_buffer + 1) % nr_buffers      

                # close audio file
                sfi.close()
                collection_audio = False

# cleanup             
print("end capturing data")

# write sound events
with open(args.soundEvent_JS, 'w') as fid:
    json.dump(soundEventList, fid, indent=2)
