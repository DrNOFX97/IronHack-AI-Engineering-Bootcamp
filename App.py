import subprocess
import sys
import os

def upgrade_pip():
    """Upgrade pip to the latest version."""
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

def install_packages(packages):
    """Install a list of packages using pip."""
    for package in packages:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

# Upgrade pip
upgrade_pip()

# List of packages to install
packages = [
    'streamlit',
    'vosk',
    'yt-dlp',
    'tqdm',
    'datasets',
    'python-dotenv',
    'openai',
    'pinecone-client',
    'tiktoken'
]

# Install the packages
install_packages(packages)

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


import streamlit as st
import yt_dlp

# Initialize yt-dlp downloader
def download_video(url):
    ydl_opts = {
        'format': 'bestaudio/best',  # Adjust format as needed
        'noplaylist': True,  # Avoid downloading playlists
        'outtmpl': 'video.%(ext)s',  # Output file template
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return 'video.mp4'  # Adjust output file name based on format
    except Exception as e:
        st.error(f"Error downloading video: {e}")
        return None

# Function to process video with sponsor handling
def process_video(url):
    st.write("Downloading video...")
    video_file = download_video(url)

    if video_file:
        st.write("Video downloaded successfully.")
        
        # Use yt-dlp to process sponsor sections or other enhancements
        # Example code to handle sponsor sections with SponSkrub
        
        # Placeholder for further processing or transcription
        st.write("Video processing completed.")

# Streamlit UI
def main():
    st.title('YouTube Video Transcription with yt-dlp')
    video_url = st.text_input("Enter YouTube video URL:")
    
    if st.button("Process Video"):
        if video_url:
            process_video(video_url)
        else:
            st.warning("Please enter a valid YouTube video URL.")

if __name__ == '__main__':
    main()
