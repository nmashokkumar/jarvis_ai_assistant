import time
import psutil
import soundfile as sf
from kokoro_onnx import Kokoro

process = psutil.Process()
ram_before = process.memory_info().rss / (1024 * 1024)

print("--- 1. Initializing Kokoro ONNX ---")
t0 = time.perf_counter()
kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
t_load = time.perf_counter() - t0
ram_after = process.memory_info().rss / (1024 * 1024)
print("Model Load Time: " + str(round(t_load, 2)) + "s | RAM used by model: " + str(round(ram_after - ram_before, 1)) + " MB")

test_text = "Done Boss, I have cancelled your 9 AM schedule. Is there anything else you want to plan for that time?"

print("\n--- 2. Synthesizing Male Voice (am_michael) ---")
t0 = time.perf_counter()
samples, sample_rate = kokoro.create(test_text, voice="am_michael", speed=1.0, lang="en-us")
t_gen_male = time.perf_counter() - t0
audio_duration = len(samples) / sample_rate
rtf = t_gen_male / audio_duration
print("Time to generate audio: " + str(round(t_gen_male, 3)) + "s (" + str(round(t_gen_male * 1000)) + " ms)")
print("Generated Audio Duration: " + str(round(audio_duration, 2)) + "s")
print("Real-Time Factor (RTF): " + str(round(rtf, 3)) + "x")
sf.write("male_output.wav", samples, sample_rate)
print("Saved male_output.wav")

print("\n--- 3. Synthesizing Female Voice (af_sarah) ---")
t0 = time.perf_counter()
samples_f, sample_rate_f = kokoro.create(test_text, voice="af_sarah", speed=1.0, lang="en-us")
t_gen_female = time.perf_counter() - t0
audio_duration_f = len(samples_f) / sample_rate_f
rtf_f = t_gen_female / audio_duration_f
print("Time to generate audio: " + str(round(t_gen_female, 3)) + "s (" + str(round(t_gen_female * 1000)) + " ms)")
print("Generated Audio Duration: " + str(round(audio_duration_f, 2)) + "s")
print("Real-Time Factor (RTF): " + str(round(rtf_f, 3)) + "x")
sf.write("female_output.wav", samples_f, sample_rate_f)
print("Saved female_output.wav")

total_ram = process.memory_info().rss / (1024 * 1024)
print("\nTotal Process RAM: " + str(round(total_ram, 1)) + " MB")
