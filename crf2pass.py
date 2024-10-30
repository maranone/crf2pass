import subprocess
import os
import argparse
from glob import glob
import tkinter as tk
from tkinter import filedialog
import re

def get_video_duration(video_file):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_file],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    return float(result.stdout)
def get_bitrate(video_file):
    bitrates = {}
    for stream_type in ["v:0", "a:0"]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream_type,
             "-show_entries", "stream=bit_rate", "-of", "default=noprint_wrappers=1:nokey=1", video_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        bitrates[stream_type[0]] = int(result.stdout) if result.stdout else None
    return bitrates["v"], bitrates["a"]
def time_to_seconds(time_str):
    h, m, s = map(float, time_str.split(':'))
    return int(h * 3600 + m * 60 + s)
def seconds_to_hhmmss(seconds):
    return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"
def monitor_ffmpeg(operation_description, process, total_duration):
    while True:
        output = process.stderr.readline()
        if not output and process.poll() is not None:
            break
        if output:
            match = re.search(r'time=(\d+:\d+:\d+\.\d+)', output)
            if match:
                current_time = time_to_seconds(match.group(1))
                if "speed=" in output:
                    speed_str = output.split("speed=")[1].split("x")[0].strip()
                    if speed_str.replace('.', '', 1).isdigit():  # Check if it's a valid float number
                        speed = float(speed_str)
                        remaining_time = total_duration - current_time
                        if speed > 0:
                            estimated_time_left = remaining_time / speed
                            completion_percentage = (current_time / total_duration) * 100 if total_duration > 0 else 0
                            print(f"\r{operation_description}, Progress: {round(completion_percentage)}%, Left: {seconds_to_hhmmss(int(estimated_time_left))}".ljust(100), end='', flush=True)
                    else:
                        print(f"\r{operation_description}, Progress: {current_time}/{total_duration} (Speed N/A)", end='', flush=True)
    process.wait()
def two_pass_encode(input_file, output_file, video_bitrate, audio_bitrate, libx_value, resx, resy, preset):
    total_duration = get_video_duration(input_file)
    base_cmd = [
        "ffmpeg", "-v", "quiet", "-stats",
        "-y", "-i", input_file,
        "-c:v", libx_value, "-b:v", f"{video_bitrate}k",
        "-vf", f"scale={resx}x{resy}:flags=lanczos", "-preset", preset
    ]
    process = subprocess.Popen(base_cmd + ["-pass", "1", "-an", "-f", "null", "NUL"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    monitor_ffmpeg(f"\rStep 5/6: Pass 1/2", process, total_duration)
    process = subprocess.Popen(base_cmd + ["-pass", "2", "-c:a", "libopus", "-b:a", f"{audio_bitrate/2}k", "-ac", "2", output_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    monitor_ffmpeg(f"\rStep 6/6: Pass 2/2", process, total_duration)
def generate_video_chunks(video_file, temp_dir, interval, chunk_duration, resx, resy, codec, crf, monitor_ffmpeg, output_file, current_step, preset):
    chunk_files = []
    for i in range(10):  # Adjust range based on how many chunks you need
        start_time = i * interval
        output_chunk = os.path.join(temp_dir, f"chunk_{i}.mp4")
        ffmpeg_cmd = [
            "ffmpeg", "-v", "quiet", "-stats",
            "-ss", str(start_time), "-i", video_file,
            "-t", str(chunk_duration),
            "-vf", f"scale={resx}x{resy}:flags=lanczos",
            "-c:v", codec, "-crf", str(crf),
            "-preset", str(preset), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2", "-q:a", "1.5",
            "-y", output_chunk
        ]
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        monitor_ffmpeg(f'\rStep {current_step}/6: Generating CRF{crf} Sample {(i+1)}/10', process, chunk_duration)
        chunk_files.append(output_chunk)
    with open("concat_list.txt", "w") as f:
        for chunk in chunk_files:
            f.write(f"file '{chunk}'\n")
    ffmpeg_concat_cmd = [
        "ffmpeg", "-v", "quiet", "-stats", "-y", "-f", "concat", "-safe", "0", "-i", "concat_list.txt",
        "-c", "copy", output_file
    ]
    process = subprocess.Popen(ffmpeg_concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    monitor_ffmpeg(f'\rStep {current_step+1}/6: Concatenating CRF{crf} Sample', process, (chunk_duration * 10))
    for chunk in chunk_files:
            os.remove(chunk)

def calculate_psnr(file1, file2):
    psnr_cmd = "ffmpeg", "-i", file1, "-i", file2, "-filter_complex", "[0:v][1:v]psnr;[0:v][1:v]ssim", "-f", "null", "-"
    result = subprocess.run(psnr_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    psnr_ssim_output = result.stderr
    psnr_line = None
    ssim_line = None
    for line in psnr_ssim_output.splitlines():
        if "average:" in line and "PSNR" in line:
            psnr_value = float(line.split("average:")[1].split()[0])
        elif "All:" in line and "SSIM" in line:
            ssim_value = float(line.split("All:")[1].split()[0])
    if psnr_value and ssim_value:
        normalized_psnr = min(psnr_value / 50 * 100, 100)
        normalized_ssim = ssim_value * 100
        combined_score = (normalized_psnr + normalized_ssim) / 2
        return f"PSNR: {round(normalized_psnr)}/100, SSIM: {round(normalized_ssim)}/100, Quality Score: {round(combined_score)}/100"
    else:
        return None
def start_sampling(input_path, percentage, crf, codec, resx, resy, extension, preset):
    if os.path.isfile(input_path):
        video_files = [input_path]
    else:
        video_files = glob(os.path.join(input_path, extension))
    file_count = len(video_files)
    current_file = 0
    for video_file in video_files:
        current_file += 1
        duration = get_video_duration(video_file)
        sample_percentage = float(percentage) / 100
        total_sample_duration = duration * sample_percentage
        chunk_duration = total_sample_duration / 10
        print(f'\rFile {current_file}/{file_count}: {video_file}, Video: {int(duration // 3600):02}:{int((duration % 3600) // 60):02}:{int(duration % 60):02}, Chunk: {round(chunk_duration)}, Sampling: {sample_percentage * 100}%')
        interval = duration / 10
        temp_dir = "temp_chunks"
        os.makedirs(temp_dir, exist_ok=True)
        output_file = "sampled_output.mp4"
        crf0_file = "crf0_output.mp4"
        generate_video_chunks(video_file, temp_dir, interval, chunk_duration, resx, resy, codec, crf, monitor_ffmpeg, output_file, 1, preset)
        generate_video_chunks(video_file, temp_dir, interval, chunk_duration, resx, resy, codec, 0, monitor_ffmpeg, crf0_file, 3, "ultrafast")
        psnr_result = calculate_psnr(crf0_file, output_file)
        video_bitrate, audio_bitrate = get_bitrate(output_file)
        print(f'\rResults for CRF{str(crf)}, VB:{round(video_bitrate / 1000)}k AB:{round(audio_bitrate / 1000)}k, {psnr_result}\n'.ljust(100), end='', flush=True)
        os.rmdir(temp_dir)
        list(map(os.remove, ["concat_list.txt", output_file, crf0_file]))
        base_name, ext = os.path.splitext(video_file)
        final_output_file = f"{base_name}_v{round(video_bitrate / 1000)}k_a{round(audio_bitrate / 2000)}k.mp4"
        two_pass_encode(video_file, final_output_file, round(video_bitrate / 1000), round(audio_bitrate / 1000), codec, resx, resy, preset)
        [os.remove(file) for file in ["ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"] if os.path.exists(file)]
    print(f'\rGetting this message probably means the program somehow managed to get to the last line of code'.ljust(100), end='', flush=True)
def get_user_inputs():
    root = tk.Tk()
    root.withdraw()
    input_path = filedialog.askopenfilename(title="Select a Video File or Folder")
    percentage = float(input("Enter Sample Percentage (1-100): "))
    crf = int(input("Enter CRF Value (0-51): "))
    codec = input("Enter Codec (libx264 or libx265): ")
    resx = int(input("Enter Horizontal Resolution (e.g., 640): "))
    resy = int(input("Enter Vertical Resolution (e.g., 480): "))
    preset = input("Enter Preset (e.g., ultrafast, fast, medium, slow, veryslow): ")
    return input_path, percentage, crf, codec, resx, resy, preset
parser = argparse.ArgumentParser(description="CRF (to) 2 Pass: Generating a 2 Pass with Bitrate from CRF Sampling")
parser.add_argument("input_path", nargs="?", help="Path to a video file or folder containing video files")
parser.add_argument("-p", "--percentage", type=float, default=5.0, help="Sample percentage (1-100)")
parser.add_argument("-c", "--crf", type=int, default=28, help="CRF value (0-51)")
parser.add_argument("-l", "--codec", default="libx264", help="Codec (libx264 or libx265)")
parser.add_argument("-x", "--resx", type=int, default=640, help="Horizontal resolution (e.g., 640)")
parser.add_argument("-y", "--resy", type=int, default=480, help="Vertical resolution (e.g., 480)")
parser.add_argument("-e", "--extension", default="*.mp4", help="Extensions to process")
parser.add_argument("-s", "--preset", default="veryslow", help="ultrafast, fast, medium, slow, veryslow")
args = parser.parse_args()
if not args.input_path:
        print(f'Unattended usage example: crf2pass ./video_input_folder --crf 21 --resx 1024 --resy 768 --percentage 10')
        args.input_path, args.percentage, args.crf, args.codec, args.resx, args.resy, args.preset = get_user_inputs()
        if not args.input_path:
            print("No file selected, exiting.")
input_path = args.input_path
percentage = args.percentage
crf = args.crf
codec = args.codec
resx = args.resx
resy = args.resy
extension = args.extension
preset = args.preset
start_sampling(input_path, percentage, crf, codec, resx, resy, extension, preset)
