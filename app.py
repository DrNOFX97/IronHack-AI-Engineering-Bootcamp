import subprocess

import os
import re
import json
import time
import wave
import yt_dlp
import sqlite3
import pinecone
import tiktoken
import subprocess

from uuid import uuid4
from typing import List
from tqdm.auto import tqdm

from vosk import Model, KaldiRecognizer

from pinecone import Pinecone, ServerlessSpec

from langchain.chains import RetrievalQA
from langchain_pinecone import PineconeVectorStore
from langchain.agents import Tool, initialize_agent
from langchain_openai import OpenAIEmbeddings, ChatOpenAI, OpenAI
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.conversation.memory import ConversationBufferWindowMemory

from langchain.output_parsers import ListOutputParser
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

from flask import Flask, request, jsonify, render_template

"""# Set API Keys"""

from dotenv import load_dotenv
import os

# Load environment variables from the .env file
load_dotenv()

# Get the API keys
pinecone_api_key = os.getenv('PINECONE_API_KEY')
openai_api_key = os.getenv('OPENAI_API_KEY')
langchain_api_key = os.getenv('LANGCHAIN_API_KEY')

# If running on CoLab, comment out above and uncomment below

# from google.colab import files, userdata

# PINECONE_API_KEY=userdata.get('PINECONE_API_KEY')
# OPENAI_API_KEY=userdata.get('OPENAI_API_KEY')
# LANGCHAIN_API_KEY=userdata.get('LANGCHAIN_API_KEY')

"""# Speech-to-text
- ### User input URL
- ### Download YouTube video upon user URL input
- ### Extract and transcribe audio
"""

# Add the directory containing ffmpeg to the PATH
os.environ['PATH'] += os.pathsep + '/usr/local/bin'

def is_valid_youtube_url(url):
    pattern = r'^(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+$'
    return re.match(pattern, url) is not None

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

# Get video URL from user
while True:
    video_url = input("Enter the YouTube video URL: ")
    if is_valid_youtube_url(video_url):
        break
    else:
        print("Invalid YouTube URL. Please enter a valid URL.")

# Download video
print("Downloading video...")
audio_file = download_video(video_url)

if audio_file and os.path.exists(audio_file):
    # Transcribe audio
    print("Transcribing audio...")
    transcription = transcribe_audio(audio_file)

    if transcription:
        # Print transcription
        print("\nTranscription:")
        print(transcription)

        # Save transcription to a file (overwrite if exists)
        with open('transcription.txt', 'w') as f:
            f.write(transcription)

        print("Transcription saved to 'transcription.txt'.")
    else:
        print("Transcription failed. Please check the error messages above.")
else:
    print("Failed to download the video. Please check the URL and try again.")

"""# Load data into database"""

# Define the path to the transcription file
transcription_file = 'transcription.txt'

# Read the transcription text
with open(transcription_file, 'r') as file:
    transcription_text = file.read()

# Connect to SQLite database (or create it if it doesn't exist)
conn = sqlite3.connect('transcriptions.db')
cursor = conn.cursor()

# Create the transcriptions table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS transcriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        speaker TEXT,
        text TEXT,
        timestamp TEXT
    )
''')

# Insert the transcription text into the table
cursor.execute('''
    INSERT INTO transcriptions (speaker, text, timestamp)
    VALUES (?, ?, ?)
''', ('Transcript', transcription_text, '2024-07-15 10:00:00'))

# Commit the transaction and close the connection
conn.commit()
conn.close()

print("Data has been successfully loaded into the database.")

# Connect to the SQLite database
conn = sqlite3.connect('transcriptions.db')
cursor = conn.cursor()

# Query the data
cursor.execute('SELECT * FROM transcriptions')
rows = cursor.fetchall()

for row in rows:
    print(row)

conn.close()

tiktoken.encoding_for_model('gpt-3.5-turbo')

# Function to calculate the length of text in terms of tokens
def tiktoken_len(text):
    return len(text.split())

# Connect to the SQLite database
conn = sqlite3.connect('transcriptions.db')
cursor = conn.cursor()

# Query the data
cursor.execute('SELECT text FROM transcriptions WHERE id = 1')
transcription_text = cursor.fetchone()[0]

# Close the connection
conn.close()

# Initialize the text splitter
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=20,
    length_function=tiktoken_len
)

# Split the transcription text into chunks
chunks = text_splitter.split_text(transcription_text)[:3]

# Print the first 3 chunks
for i, chunk in enumerate(chunks, 1):
    print(f"Chunk {i}:")
    print(chunk)
    print()

# transcription_text

tiktoken_len(chunks[0]), tiktoken_len(chunks[1]), tiktoken_len(chunks[2])

"""# Text embedding"""

model_name = 'text-embedding-ada-002'

embed = OpenAIEmbeddings(
    model=model_name,
    openai_api_key=openai_api_key
)

"""# Indexing"""

# configure client
pc = Pinecone(api_key=pinecone_api_key)

spec = ServerlessSpec(
    cloud="aws", region="us-east-1"
)

index_name = 'langchain-retrieval-augmentation'
existing_indexes = [
    index_info["name"] for index_info in pc.list_indexes()
]

# check if index already exists (it shouldn't if this is first time)
if index_name not in existing_indexes:
    # if does not exist, create index
    pc.create_index(
        index_name,
        dimension=1536,  # dimensionality of ada 002
        metric='dotproduct',
        spec=spec
    )
    # wait for index to be initialized
    while not pc.describe_index(index_name).status['ready']:
        time.sleep(1)

# connect to index
index = pc.Index(index_name)
time.sleep(1)
# view index stats
index.describe_index_stats()

"""# Data processing"""

batch_limit=50

# Initialize text splitter
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=20,
    length_function=len  # You can replace with your custom token length function if needed
)

# Connect to SQLite database
conn = sqlite3.connect('transcriptions.db')
cursor = conn.cursor()

# Fetch data from SQLite database
cursor.execute('SELECT id, speaker, text FROM transcriptions')
data = cursor.fetchall()

# Close the database connection
conn.close()

# Initialize lists for texts and metadatas
texts = []
metadatas = []

# Process each record fetched from the database
for i, record in enumerate(tqdm(data)):
    # Metadata fields for this record
    metadata = {
        #'index': str(record[0]),  # Assuming id is the first column
        'speaker': record[1],  # Replace with actual source if available
        'text': record[2],  # Example title based on id
    }

    # Split text into chunks
    record_texts = text_splitter.split_text(record[2])  # Assuming text is the third column

    # Create individual metadata dicts for each chunk
    record_metadatas = [{
        "chunk": j,
        "text": text,
        **metadata
    } for j, text in enumerate(record_texts)]

    # Append texts and metadatas to current batches
    texts.extend(record_texts)
    metadatas.extend(record_metadatas)

    # Check if batch limit is reached, then embed and upsert
    if len(texts) >= batch_limit:
        ids = [str(uuid4()) for _ in range(len(texts))]
        embeds = embed.embed_documents(texts)

        # Assuming `index` is where you want to upsert (not defined in the snippet)
        index.upsert(vectors=zip(ids, embeds, metadatas))

        # Clear lists after upserting
        texts = []
        metadatas = []

# Process any remaining texts in the lists
if len(texts) > 0:
    ids = [str(uuid4()) for _ in range(len(texts))]
    embeds = embed.embed_documents(texts)
    index.upsert(vectors=zip(ids, embeds, metadatas))

print("Data processing completed.")

index.describe_index_stats()

"""# Initialize Pinecone Vector Store"""

# Define the metadata field that contains your text
text_field = 'text'

# Initialize the Pinecone vector store object
vectorstore = PineconeVectorStore(index, embed, text_field)

query = input("Please enter your query: ")

vectorstore.similarity_search(
    query,  # our search query
    k=3  # return 3 most relevant docs
)

"""# Retrieve and set memory"""

# completion llm
llm = ChatOpenAI(
    openai_api_key=openai_api_key,
    model_name='gpt-3.5-turbo',
    temperature=0.0
)

# conversational memory
conversational_memory = ConversationBufferWindowMemory(
    memory_key='chat_history',
    k=5,
    return_messages=True
    )

# retrieval qa chain
qa = RetrievalQA.from_chain_type(
    llm=llm,
    chain_type="stuff",
    retriever=vectorstore.as_retriever()
)

qa.invoke(query)

"""# Multi querying"""

tools = [
    Tool(
        name='Knowledge Base',
        func=qa.invoke,
        description=(
            'use this tool when answering general knowledge queries to get '
            'more information about the topic'
        )
    )
]

from langchain.agents import initialize_agent

agent = initialize_agent(
    agent='chat-conversational-react-description',
    tools=tools,
    llm=llm,
    verbose=True,
    max_iterations=3,
    early_stopping_method='generate',
    memory=conversational_memory
)

"""# Asking questions concerning the video"""

agent(query)

agent("what is a qubit?")

agent("How fast is a quantum computer?")

"""# Ask a complete random question not related at all with the video"""

agent("history of portugal in XV century")

import pinecone
import time

# Initialize Pinecone client directly
pinecone_client = pinecone.Pinecone(api_key=pinecone_api_key, environment='us-west1-gcp')

index_name = 'langchain-multi-query'

if index_name not in pinecone_client.list_indexes().names():  # Use .names() to get list of index names
    # Define index configuration
    index_spec = {
        'dimension': 1536,
        'metric': 'cosine'
    }
    pinecone_client.create_index(name=index_name, **index_spec)  # Pass index name as 'name'
    while not pinecone_client.describe_index(index_name).status['ready']:
        time.sleep(1)

index = pinecone_client.Index(index_name)

len(texts)

batch_size = 1

for i in tqdm(range(0, len(texts), batch_size)):
    i_end = min(i+batch_size, len(texts))
    ids = [str(uuid4()) for _ in range(i_end-i)]
    embeds = embed.embed_documents(texts[i:i_end])
    index.upsert(vectors=zip(ids, embeds, metadatas[i:i_end]))
    time.sleep(1)

text_field = "text"

vectorstore = PineconeVectorStore(index, embed, text_field)

llm = ChatOpenAI(temperature=0.0, openai_api_key=openai_api_key)

from langchain.retrievers.multi_query import MultiQueryRetriever

retriever = MultiQueryRetriever.from_llm(retriever=vectorstore.as_retriever(), llm=llm)

import logging

logging.basicConfig()
logging.getLogger("langchain.retrievers.multi_query").setLevel(logging.INFO)

question = "How fast is a quantum computer?"

texts = retriever.invoke(input=question)
len(texts)

#texts

from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

QA_PROMPT = PromptTemplate(
    input_variables=["query", "contexts"],
    template="""You are a helpful assistant who answers user queries using the contexts provided. If the question cannot be answered using the information provided say "I don't know".

    Contexts:
    {contexts}

    Question: {query}
    Answer:"""
)

qa_chain = LLMChain(llm=llm, prompt=QA_PROMPT, verbose=False)

output = qa_chain(inputs={"query": question, "contexts": "\n---\n".join([d.page_content for d in texts])})
print(output["text"])

from langchain.chains import TransformChain

def retrieval_transform(inputs: dict) -> dict:
    texts = retriever.get_relevant_documents(query=inputs["question"])
    texts = [d.page_content for d in texts]
    texts_dict = {
        "query": inputs["question"],
        "contexts": "\n---\n".join(texts)
    }
    return texts_dict

retrieval_chain = TransformChain(
    input_variables=["question"],
    output_variables=["query", "contexts"],
    transform=retrieval_transform
)

from langchain.chains import SequentialChain

rag_chain = SequentialChain(
    chains=[retrieval_chain, qa_chain],
    input_variables=["question"],
    output_variables=["query", "contexts", "text"],
    verbose=True
)

output = rag_chain({"question": question})
print(output["text"])

"""# Custom Multiquery

## Prompt A
'''Your task is to generate 3 different queries that aim to answer the user question from multiple perspectives.
Every query MUST tackle the question from a different viewpoint, we want to get a variety of RELEVANT search results.
Provide these alternative questions separated by newlines.
Original question: {question}'''

## Prompt B
'''Your task is to generate 3 different search queries that aim to answer the question from multiple perspectives. The user questions are focused on Quantum Computing, AI, future technology and related subjects.
Every query MUST tackle the question from a different viewpoint, we want to get a variety of RELEVANT search results.
Provide these alternative questions separated by newlines.
Original question: {question}'''
"""

class SimpleListOutputParser(ListOutputParser):
    def parse(self, text: str) -> List[str]:
        # Split the text into lines and handle potential empty strings
        return [line.strip() for line in text.split("\n") if line.strip()]

output_parser = SimpleListOutputParser()

template = """
Your task is to generate 3 different queries that aim to answer the user question from multiple perspectives.
Every query MUST tackle the question from a different viewpoint, we want to get a variety of RELEVANT search results.
Provide these alternative questions separated by newlines.
Original question: {question}
"""

QUERY_PROMPT = PromptTemplate(
    input_variables=["question"],
    template=template
)

llm = OpenAI(temperature=0.3, openai_api_key=openai_api_key)

llm_chain = LLMChain(llm=llm, prompt=QUERY_PROMPT, output_parser=output_parser)

# Debug: Test the LLMChain output
test_question = "What are the effects of climate change?"
result = llm_chain.invoke(test_question)
print("LLMChain output:", result)

# Assuming vectorstore is defined elsewhere
retriever = MultiQueryRetriever(
    retriever=vectorstore.as_retriever(),
    llm_chain=llm_chain,
    parser_key="lines"  # This should match the attribute name in the output parser
)

# Debug: Print the type and content of 'question'
print("Type of question:", type(question))
print("Content of question:", question)

texts = retriever.get_relevant_documents(query=question)
print("Number of retrieved texts:", len(texts))

class SimpleListOutputParser(ListOutputParser):
    def parse(self, text: str) -> List[str]:
        # Split the text into lines and handle potential empty strings
        return [line.strip() for line in text.split("\n") if line.strip()]

output_parser = SimpleListOutputParser()

template = """
Your task is to generate 3 different search queries that aim to answer the question from multiple perspectives. The user questions are focused on Quantum Computing, AI, future technology and related subjects.
Every query MUST tackle the question from a different viewpoint, we want to get a variety of RELEVANT search results.
Provide these alternative questions separated by newlines.
Original question: {question}
"""

QUERY_PROMPT = PromptTemplate(
    input_variables=["question"],
    template=template
)

llm = OpenAI(temperature=0.3, openai_api_key=openai_api_key)

llm_chain = LLMChain(llm=llm, prompt=QUERY_PROMPT, output_parser=output_parser)

# Debug: Test the LLMChain output
test_question = "What are the effects of climate change?"
result = llm_chain.invoke(test_question)
print("LLMChain output:", result)

# Assuming vectorstore is defined elsewhere
retriever = MultiQueryRetriever(
    retriever=vectorstore.as_retriever(),
    llm_chain=llm_chain,
    parser_key="lines"  # This should match the attribute name in the output parser
)

# Debug: Print the type and content of 'question'
print("Type of question:", type(question))
print("Content of question:", question)

texts = retriever.get_relevant_documents(query=question)
print("Number of retrieved texts:", len(texts))

"""# Setup Flask for Web Interface"""

# Initialize Flask app
app = Flask(__name__)
app.debug = False  # Enable debug mode

# Initialize the agent, llm, and memory
# Replace placeholders (tools, llm, etc.) with your actual initialization code
agent = initialize_agent(
    agent='chat-conversational-react-description',
    tools=tools,
    llm=llm,
    verbose=True,
    max_iterations=3,
    early_stopping_method='generate',
    memory=conversational_memory
)

# Initialize conversation memory
conversational_memory = ConversationBufferWindowMemory(
    memory_key='chat_history',
    k=5,
    return_messages=True
)

# Route to render the main page
@app.route('/')
def index():
    return render_template('index.html')

# Route to handle user queries
@app.route('/ask', methods=['POST'])
def ask():
    user_input = request.form['query']
    response = agent(user_input)
    conversational_memory.add_message(user_input, response['output'])  # Store conversation history
    return jsonify({'response': response['output']})

if __name__ == '__main__':
    app.run(port=5000)  # Change 5000 to your desired port number