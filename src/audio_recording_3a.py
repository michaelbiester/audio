# audio_recording_3a.py

"""
recording audio samples and saving into an audio file

a number of audio buffers is preallocated and then used in 
a circular fashion:

1) collect audio samples in buffer Nr.1
2) repeat with buffer Nr. 2 etc.
3) after having written data to the last buffer , repeat the procedure starting with buffer Nr. 1

A maximum number (configurable) of write cycle is used. The program exits after this number of write cycles
has been performed

The main difference with program audio_recording_2.py

is that a simple sound activity detectors has been included.

If required a set of audio buffers is written to a file
"""

import soundfile as sf
import sounddevice as sd
import numpy as np
import queue
from functools import partial


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

            base_wav, wav_ext = os.path.splitext(args.outAudioWav)
        except:
            sys.exit('invalid configuration')

    # each buffer shall have these number of samples 
    nr_samples_buf = int(buffer_duration_s * samplerate_hz)

    # preallocation of memory (slightly larger than required)
    audio_buffers = [np.zeros(nr_samples_buf, dtype=np.float32) for k in range(nr_buffers)]
    # samples in each buffer are stored in this array
    nr_samples_buffer = np.zeros(nr_buffers, dtype=np.uint32)

    # a queuer into which the callback function stores audio samples
    q = queue.Queue()

    # the callback (uses numpy interally)
    # -> the main purpose of the callback is to transfer audio samples to a queue
    #    any further processing is done outside the callback function
    def callback_ref(q, indata, frames, time, status):
        if status:
            print(status)
        q.put(indata.copy())
    
    # wrap the callback -> the wrapped function has the signature <indata, frames, time, status> 
    wrapped_callback = partial(callback_ref, q)

    idx = 0
    count_samples = 0
    buffer_id = 0
    nr_runs = 0

    sound_activity = False
    # the processing of audio data is done in the while loop thus freeing resources 
    # from the callback function
    with sd.InputStream(samplerate=samplerate_hz, device=1, channels=nr_channels, callback=wrapped_callback) as inp:
        while True:
            data = q.get()

            # a simple sound activity detector
            if not sound_activity:
                sound_activity_score  = np.sum(np.abs(data))
                if sound_activity_score >= sound_activity_threshold:
                    sound_activity = True
                    # capture audio data into a file
                    # start with the current buffer 
                    buffer_id_start = buffer_id
                    buffer_id_stop = (buffer_id + nr_records - 1) %  nr_buffers 

                    file_wav = base_wav + f"_{buffer_id}_{nr_runs}" + wav_ext
                    sfi = sf.SoundFile(file_wav, mode='w', samplerate=samplerate_hz, channels=nr_channels)
                    print(f"record to file: {file_wav}")

            ndata = len(data)
            count_samples += ndata
            
            if count_samples >= nr_samples_buf:
                count_samples = ndata
                idx = 0

                # write current buffer to file
                if sound_activity:
                    sfi.write(audio_buffers[buffer_id][:nr_samples_buffer[buffer_id]])
                    
                    if buffer_id == buffer_id_stop:
                        sfi.close()
                        sound_activity = False
                        print(f"listening to new sound event")

                # put data into current buffer   
                nr_runs += 1
                buffer_id = (buffer_id + 1) % nr_buffers
                 
            audio_buffers[buffer_id][idx:idx + ndata] = data[:,0]
            idx = idx + ndata
            nr_samples_buffer[buffer_id] = idx                
            # print(f"cpu_load: {inp.cpu_load}")
        
            # at most this number of buffers are recorded
            # if there is still an open audio file it must be closed ...
            if nr_runs >= args.nr_cycles:
                if sound_activity:
                    sfi.close()
                    sound_activity = False

                break
        
    print("end capturing data")
