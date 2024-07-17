import streamlit as st
import subprocess
import sys
import os
import re
import json
import time
import wave
import sqlite3
import yt_dlp
from tqdm.auto import tqdm
from vosk import Model, KaldiRecognizer
from dotenv import load_dotenv
from langchain.chains import RetrievalQA
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.conversation.memory import ConversationBufferWindowMemory
from langchain.output_parsers import ListOutputParser
from langchain.chains import LLMChain
from langchain.agents import Tool, initialize_agent
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain.prompts import PromptTemplate
import pinecone
from uuid import uuid4

# Load environment variables
load_dotenv()

# Function to install packages using pip
def install_packages(packages):
    subprocess.check_call([sys.executable, "-m", "pip", "install", *packages])

# List of packages to install
packages = [
    'vosk', 'yt-dlp', 'tqdm', 'datasets', 'openai', 'pinecone-client', 'tiktoken',
    'pyarrow==11.0.0', 'flask', 'streamlit', 'python-dotenv'
]

# Install the packages
install_packages(packages)

# Download Vosk model if not present
if not os.path.exists("model"):
    subprocess.check_call(["wget", "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"])
    subprocess.check_call(["unzip", "vosk-model-small-en-us-0.15.zip"])
    subprocess.check_call(["mv", "vosk-model-small-en-us-0.15", "model"])
    subprocess.check_call(["rm", "vosk-model-small-en-us-0.15.zip"])

# Function to convert audio format
def convert_audio(input_file, output_file):
    command = [
        '/usr/local/bin/ffmpeg',  # Explicitly use the full path to ffmpeg
        '-i', input_file,
        '-acodec', 'pcm_s16le',
        '-ac', '1',
        '-ar', '16000',
        output_file
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error converting audio: {e}")
        return False

# Function to transcribe audio
def transcribe_audio(audio_file):
    if not os.path.exists("model"):
        print("Speech recognition model not found. Please make sure you've downloaded it.")
        return None

    model = Model("model")
    rec = KaldiRecognizer(model, 16000)

    try:
        wf = wave.open(audio_file, "rb")
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getcomptype() != "NONE":
            print("Converting audio to the correct format...")
            converted_file = "converted_audio.wav"
            if not convert_audio(audio_file, converted_file):
                return None
            wf = wave.open(converted_file, "rb")

        results = []
        total_frames = wf.getnframes()
        with tqdm(total=total_frames, desc="Transcribing") as pbar:
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                if rec.AcceptWaveform(data):
                    part_result = json.loads(rec.Result())
                    results.append(part_result['text'])
                pbar.update(4000)

        part_result = json.loads(rec.FinalResult())
        results.append(part_result['text'])
        return " ".join(results)
    except Exception as e:
        print(f"An error occurred during transcription: {str(e)}")
        return None

# Function to download video
def download_video(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
        'outtmpl': 'audio.%(ext)s',
        'ffmpeg_location': '/usr/local/bin'  # Explicitly set the ffmpeg location
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return 'audio.wav'
    except Exception as e:
        print(f"An error occurred during download: {str(e)}")
        return None

# Function to check valid YouTube URL
def is_valid_youtube_url(url):
    pattern = r'^(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+$'
    return re.match(pattern, url) is not None

# Function to initialize Pinecone Vector Store
def initialize_vectorstore():
    pinecone_api_key = os.getenv('PINECONE_API_KEY')
    openai_api_key = os.getenv('OPENAI_API_KEY')

    embed = OpenAIEmbeddings(
        model='text-embedding-ada-002',
        openai_api_key=openai_api_key
    )

    pc = pinecone.Pinecone(api_key=pinecone_api_key)

    spec = pinecone.ServerlessSpec(
        cloud="aws", region="us-east-1"
    )

    index_name = 'langchain-retrieval-augmentation'
    existing_indexes = [index_info["name"] for index_info in pc.list_indexes()]

    if index_name not in existing_indexes:
        pc.create_index(index_name, dimension=1536, metric='dotproduct', spec=spec)
        while not pc.describe_index(index_name).status['ready']:
            time.sleep(1)

    index = pc.Index(index_name)
    return index, embed

# Initialize Pinecone Vector Store and Streamlit
index, embed = initialize_vectorstore()
st.title('Conversational Agent')

# Function to transcribe and process video
def process_video():
    video_url = st.text_input("Enter the YouTube video URL:")
    if st.button("Transcribe"):
        if is_valid_youtube_url(video_url):
            st.write("Downloading video...")
            audio_file = download_video(video_url)

            if audio_file and os.path.exists(audio_file):
                st.write("Transcribing audio...")
                transcription = transcribe_audio(audio_file)

                if transcription:
                    st.write("\nTranscription:")
                    st.write(transcription)

                    with open('transcription.txt', 'w') as f:
                        f.write(transcription)

                    st.write("Transcription saved to 'transcription.txt'.")
                else:
                    st.write("Transcription failed. Please check the error messages above.")
            else:
                st.write("Failed to download the video. Please check the URL and try again.")
        else:
            st.write("Invalid YouTube URL. Please enter a valid URL.")

# Function to load transcription into database
def load_transcription_to_database(transcription_text):
    transcription_file = 'transcription.txt'
    with open(transcription_file, 'r') as file:
        transcription_text = file.read()

    conn = sqlite3.connect('transcriptions.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            speaker TEXT,
            text TEXT,
            timestamp TEXT
        )
    ''')

    cursor.execute('''
        INSERT INTO transcriptions (speaker, text, timestamp)
        VALUES (?, ?, ?)
    ''', ('Transcript', transcription_text, '2024-07-15 10:00:00'))

    conn.commit()
    conn.close()

    st.write("Data has been successfully loaded into the database.")

# Function to process user query and retrieve response
def process_query(query):
    vectorstore = initialize_vectorstore()[0]
    retriever = RetrievalQA.from_chain_type(
        llm=ChatOpenAI(openai_api_key=os.getenv('OPENAI_API_KEY')),
        chain_type="stuff",
        retriever=vectorstore.as_retriever()
    )
    return retriever.invoke(query)

# User interaction loop
def main():
    user_choice = st.sidebar.selectbox("Select an action", ["Transcribe Video", "Load Transcription", "Query Agent"])

    if user_choice == "Transcribe Video":
        process_video()
    elif user_choice == "Load Transcription":
        transcription_text = st.text_area("Paste transcription text:")
        if st.button("Load into Database"):
            load_transcription_to_database(transcription_text)
    elif user_choice == "Query Agent":
        query = st.text_input("Ask a question:")
        if st.button("Ask"):
            response = process_query(query)
            st.write("Agent Response:", response)

if __name__ == '__main__':
    main()
