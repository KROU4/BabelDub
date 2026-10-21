"""Audio helpers shared by the pipeline: WAV I/O, DSP (resample, trim, loudness) and time-stretch.

Arrays are float32. Multi-channel audio is shaped [frames, channels]; most DSP helpers also
accept a 1-D mono array and return the same shape they were given.
"""
