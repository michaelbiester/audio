# audio_recording_1.py

"""
recording audio samples and saving into an audio file
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
    parser.add_argument('duration_s', type=float, help="recording duration in seconds")
    parser.add_argument('outAudioWav', help="audio output file (*.wav)")

    args = parser.parse_args()

    with open(args.configJS, mode='r') as cfg:
        config_D = json.load(cfg)

        try:
            print("processing configuration")
            device_index = config_D["device_index"]
            nr_channels = config_D["channels"]
            samplerate_hz = config_D["samplerate_hz"]
        except:
            sys.exit('invalid configuration')

    #
    nr_samples = int(args.duration_s * samplerate_hz)
    # preallocation of memory (slightly larger than required)
    audio_samples = np.zeros(nr_samples, dtype=np.float32)
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

    print("begin capturing data")
    idx = 0
    count_samples = 0
    # the processing of audio data is done in the while loop thus freeing resources 
    # from the callback function
    with sd.InputStream(samplerate=samplerate_hz, device=device_index, channels=nr_channels, callback=wrapped_callback):
        while True:
            data = q.get()
            ndata = len(data)
            count_samples += ndata
            if count_samples >= nr_samples:
                break
            audio_samples[idx:idx + ndata] = data[:,0]
            idx = idx + ndata
        
print("end capturing data")

# save to audio file
with sf.SoundFile(args.outAudioWav, mode='w', samplerate=samplerate_hz, channels=nr_channels) as sfi:
    sfi.write(audio_samples)
    print(f"saved audio data to file: {args.outAudioWav}")
    # closing is done implicitely by the context manager
