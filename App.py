import streamlit as st

st.title('Conversational Agent')

# Function to check if a package is installed
def is_package_installed(package_name):
    try:
        __import__(package_name)
        return True
    except ImportError:
        return False

# List of required packages
required_packages = ['yt_dlp', 'tqdm', 'vosk', 'dotenv', 'langchain', 'openai', 'pinecone']

# Check for missing packages
missing_packages = [pkg for pkg in required_packages if not is_package_installed(pkg)]

if missing_packages:
    st.error(f"The following required packages are not installed: {', '.join(missing_packages)}")
    st.info("Please make sure these packages are listed in your requirements.txt file.")
    st.info("If you're using Streamlit Cloud, check your deployment logs for any installation errors.")
    st.stop()

# If all packages are installed, we can safely import them
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Check for API keys
pinecone_api_key = os.getenv('PINECONE_API_KEY')
openai_api_key = os.getenv('OPENAI_API_KEY')

if not pinecone_api_key or not openai_api_key:
    st.error("API keys not found. Please check your .env file or Streamlit Cloud secrets.")
    st.stop()

st.success("All required packages are installed and API keys are found.")

# Basic functionality demo
option = st.selectbox(
    'What would you like to do?',
    ('Transcribe Video', 'Load Transcription', 'Query Agent')
)

if option == 'Transcribe Video':
    st.write('You selected Transcribe Video')
    video_url = st.text_input("Enter YouTube URL:")
    if st.button("Transcribe"):
        st.write(f"Transcribing video from: {video_url}")
        # Add your transcription logic here

elif option == 'Load Transcription':
    st.write('You selected Load Transcription')
    transcription = st.text_area("Enter transcription:")
    if st.button("Load"):
        st.write(f"Loading transcription: {transcription[:100]}...")
        # Add your loading logic here

elif option == 'Query Agent':
    st.write('You selected Query Agent')
    query = st.text_input("Enter your query:")
    if st.button("Ask"):
        st.write(f"Processing query: {query}")
        # Add your query processing logic here

st.write("App is running successfully!")