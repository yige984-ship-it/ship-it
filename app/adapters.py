"""Replace these protocols with provider implementations in phase two."""
from pathlib import Path
from typing import Protocol
import subprocess

class ImageAdapter(Protocol):
    def generate(self, prompt: str, output: Path) -> Path: ...

class VoiceAdapter(Protocol):
    def generate(self, text: str, duration: int, output: Path) -> Path: ...

class VideoAdapter(Protocol):
    def generate(self, image: Path, duration: int, output: Path) -> Path: ...

def run(args):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args],
                   check=True, capture_output=True, timeout=180)

class MockImage:
    def generate(self, prompt, output):
        run(['-f', 'lavfi', '-i', 'color=c=0x17243b:s=360x640', '-frames:v', '1', '-threads', '1', str(output)])
        return output

class MockVoice:
    def generate(self, text, duration, output):
        # Silence is deliberately not represented as synthesized speech.
        run(['-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono', '-t', str(duration), str(output)])
        return output

class MockVideo:
    def generate(self, image, duration, output):
        run(['-loop', '1', '-i', str(image), '-t', str(duration), '-vf',
             "drawtext=text='SIMULATION - NO AI VIDEO':fontcolor=white:fontsize=18:x=(w-tw)/2:y=h/2",
             '-r', '12', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-threads', '1', str(output)])
        return output

class KeyframeAnimation:
    """Local 5-second motion preview; no video model call or speech synthesis."""
    def generate(self, image: Path, output: Path):
        run(['-i', str(image), '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono',
             '-vf', "scale=360:640:force_original_aspect_ratio=increase,crop=360:640,"
                    "zoompan=z='min(zoom+0.0006,1.08)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=60:s=360x640:fps=12,"
                    "drawtext=text='KEYFRAME ANIMATION - NO AI VIDEO':fontcolor=white:fontsize=13:x=(w-tw)/2:y=h-40",
             '-t', '5', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-threads', '1', '-movflags', '+faststart', str(output)])
        return output
