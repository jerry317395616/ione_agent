import asyncio
import json
import math
import struct
import wave
from pathlib import Path

import edge_tts


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "platform" / "platform-film.source.json"
AUDIO_DIR = ROOT / "assets" / "platform" / "audio"
VOICE_DIR = AUDIO_DIR / "voice"
VOICE = "zh-CN-YunyangNeural"


async def generate_voice(code: str, text: str) -> None:
    output = VOICE_DIR / f"{code}.mp3"
    communicate = edge_tts.Communicate(text, VOICE, rate="+10%", pitch="-2Hz", volume="+2%")
    await communicate.save(str(output))


def generate_music(duration_seconds: float) -> None:
    sample_rate = 22050
    total_samples = int((duration_seconds + 1.0) * sample_rate)
    chords = [
        (55.00, 82.41, 110.00),
        (65.41, 98.00, 130.81),
        (73.42, 110.00, 146.83),
        (49.00, 73.42, 98.00),
    ]
    output = AUDIO_DIR / "platform-bed.wav"
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(total_samples):
            t = index / sample_rate
            chord = chords[int(t // 24) % len(chords)]
            breath = 0.52 + 0.48 * math.sin(2 * math.pi * t / 12) ** 2
            pad = sum(math.sin(2 * math.pi * note * t + i * 0.4) for i, note in enumerate(chord)) / 3
            pulse_phase = t % 2.0
            pulse = math.sin(2 * math.pi * 220 * t) * math.exp(-pulse_phase * 7.5)
            shimmer = math.sin(2 * math.pi * 440 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * t / 16))
            value = 0.11 * breath * pad + 0.018 * pulse + 0.006 * shimmer
            sample = max(-32767, min(32767, int(value * 32767)))
            frames.extend(struct.pack("<h", sample))
            if len(frames) >= sample_rate * 2 * 8:
                wav.writeframesraw(frames)
                frames.clear()
        if frames:
            wav.writeframesraw(frames)


async def main() -> None:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.gather(*(generate_voice(scene["code"], scene["narration"]) for scene in data["scenes"]))
    generate_music(sum(float(scene["duration_seconds"]) for scene in data["scenes"]))
    print(f"generated {len(data['scenes'])} voice tracks and music bed in {AUDIO_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
