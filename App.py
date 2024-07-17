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

# Function to convert audio format
@st.cache_data
def convert_audio(input_file, output_file):
    command = [
        'ffmpeg',  # Assume ffmpeg is in PATH
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
        st.error(f"Error converting audio: {e}")
        return False

# Function to transcribe audio
@st.cache_data
def transcribe_audio(audio_file):
    if not os.path.exists("model"):
        st.error("Speech recognition model not found. Please make sure you've downloaded it.")
        return None

    model = Model("model")
    rec = KaldiRecognizer(model, 16000)

    try:
        wf = wave.open(audio_file, "rb")
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getcomptype() != "NONE":
            st.info("Converting audio to the correct format...")
            converted_file = "converted_audio.wav"
            if not convert_audio(audio_file, converted_file):
                return None
            wf = wave.open(converted_file, "rb")

        results = []
        total_frames = wf.getnframes()
        progress_bar = st.progress(0)
        for i in range(0, total_frames, 4000):
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                part_result = json.loads(rec.Result())
                results.append(part_result['text'])
            progress_bar.progress(min(i / total_frames, 1.0))

        part_result = json.loads(rec.FinalResult())
        results.append(part_result['text'])
        return " ".join(results)
    except Exception as e:
        st.error(f"An error occurred during transcription: {str(e)}")
        return None

# Function to download video
@st.cache_data
def download_video(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
        'outtmpl': 'audio.%(ext)s',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return 'audio.wav'
    except Exception as e:
        st.error(f"An error occurred during download: {str(e)}")
        return None

# Function to check valid YouTube URL
def is_valid_youtube_url(url):
    pattern = r'^(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+$'
    return re.match(pattern, url) is not None

# Function to initialize Pinecone Vector Store
@st.cache_resource
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
            with st.spinner("Downloading video..."):
                audio_file = download_video(video_url)

            if audio_file and os.path.exists(audio_file):
                with st.spinner("Transcribing audio..."):
                    transcription = transcribe_audio(audio_file)

                if transcription:
                    st.subheader("Transcription:")
                    st.write(transcription)

                    with open('transcription.txt', 'w') as f:
                        f.write(transcription)

                    st.success("Transcription saved to 'transcription.txt'.")
                else:
                    st.error("Transcription failed. Please check the error messages above.")
            else:
                st.error("Failed to download the video. Please check the URL and try again.")
        else:
            st.error("Invalid YouTube URL. Please enter a valid URL.")

# Function to load transcription into database
def load_transcription_to_database(transcription_text):
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

    st.success("Data has been successfully loaded into the database.")

# Function to process user query and retrieve response
@st.cache_data
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
            with st.spinner("Processing query..."):
                response = process_query(query)
            st.subheader("Agent Response:")
            st.write(response)

if __name__ == '__main__':
    main()