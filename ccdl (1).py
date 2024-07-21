import streamlit as st
import json
import locale
import os
import platform
import random
import shutil
import string
from collections import OrderedDict
from subprocess import PIPE, Popen
from xml.etree import ElementTree as ET

import requests
from tqdm.auto import tqdm

# Initialize session
session = requests.sessions.Session()

VERSION = 4
VERSION_STR = '0.2.0'

ADOBE_PRODUCTS_XML_URL = 'https://prod-rel-ffc-ccm.oobesaas.adobe.com/adobe-ffc-external/core/v{urlVersion}/products/all?_type=xml&channel=ccm&channel=sti&platform={installPlatform}&productType=Desktop'
ADOBE_APPLICATION_JSON_URL = 'https://cdn-ffc.oobesaas.adobe.com/core/v3/applications'

DRIVER_XML = '''<DriverInfo>
    <ProductInfo>
        <Name>Adobe {name}</Name>
        <SAPCode>{sapCode}</SAPCode>
        <CodexVersion>{version}</CodexVersion>
        <Platform>{installPlatform}</Platform>
        <EsdDirectory>./{sapCode}</EsdDirectory>
        <Dependencies>
{dependencies}
        </Dependencies>
    </ProductInfo>
    <RequestInfo>
        <InstallDir>/Applications</InstallDir>
        <InstallLanguage>{language}</InstallLanguage>
    </RequestInfo>
</DriverInfo>
'''

DRIVER_XML_DEPENDENCY = '''         <Dependency>
                <SAPCode>{sapCode}</SAPCode>
                <BaseVersion>{version}</BaseVersion>
                <EsdDirectory>./{sapCode}</EsdDirectory>
            </Dependency>'''

ADOBE_REQ_HEADERS = {
    'X-Adobe-App-Id': 'accc-apps-panel-desktop',
    'User-Agent': 'Adobe Application Manager 2.0',
    'X-Api-Key': 'CC_HD_ESD_1_0',
    'Cookie': 'fg=' + ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(26)) + '======'
}

ADOBE_DL_HEADERS = {
    'User-Agent': 'Creative Cloud'
}

# Helper functions
def r(url, headers=ADOBE_REQ_HEADERS):
    """Retrieve a from a url as a string."""
    req = session.get(url, headers=headers)
    req.encoding = 'utf-8'
    return req.text

def get_products_xml(adobeurl):
    """First stage of parsing the XML."""
    st.write('Source URL is: ' + adobeurl)
    return ET.fromstring(r(adobeurl))

def parse_products_xml(products_xml, urlVersion, allowedPlatforms):
    """2nd stage of parsing the XML."""
    if urlVersion == 6:
        prefix = 'channels/'
    else:
        prefix = ''
    cdn = products_xml.find(prefix + 'channel/cdn/secure').text
    products = {}
    parent_map = {c: p for p in products_xml.iter() for c in p}
    for p in products_xml.findall(prefix + 'channel/products/product'):
        sap = p.get('id')
        hidden = parent_map[parent_map[p]].get('name') != 'ccm'
        displayName = p.find('displayName').text
        productVersion = p.get('version')
        if not products.get(sap):
            products[sap] = {
                'hidden': hidden,
                'displayName': displayName,
                'sapCode': sap,
                'versions': OrderedDict()
            }

        for pf in p.findall('platforms/platform'):
            baseVersion = pf.find('languageSet').get('baseVersion')
            buildGuid = pf.find('languageSet').get('buildGuid')
            appplatform = pf.get('id')
            dependencies = list(pf.findall('languageSet/dependencies/dependency'))
            if productVersion in products[sap]['versions']:
                if products[sap]['versions'][productVersion]['apPlatform'] in allowedPlatforms:
                    break # There's no single-arch binary if macuniversal is available

            if sap == 'APRO':
                baseVersion = productVersion
                if urlVersion == 4 or urlVersion == 5:
                    productVersion = pf.find('languageSet/nglLicensingInfo/appVersion').text
                if urlVersion == 6:
                    for b in products_xml.findall('builds/build'):
                        if b.get("id") == sap and b.get("version") == baseVersion:
                            productVersion = b.find('nglLicensingInfo/appVersion').text
                            break
                buildGuid = pf.find('languageSet/urls/manifestURL').text
                # This is actually manifest URL

            products[sap]['versions'][productVersion] = {
                'sapCode': sap,
                'baseVersion': baseVersion,
                'productVersion': productVersion,
                'apPlatform': appplatform,
                'dependencies': [{
                    'sapCode': d.find('sapCode').text, 'version': d.find('baseVersion').text
                } for d in dependencies],
                'buildGuid': buildGuid
            }
    return products, cdn

def download_file(url, product_dir, s, v, name=None):
    """Download a file"""
    if not name:
        name = url.split('/')[-1].split('?')[0]
    st.write('Url is: ' + url)
    st.write('[{}_{}] Downloading {}'.format(s, v, name))
    file_path = os.path.join(product_dir, name)
    response = session.head(url, stream=True, headers=ADOBE_DL_HEADERS)
    total_size_in_bytes = int(
        response.headers.get('content-length', 0))
    if os.path.isfile(file_path) and os.path.getsize(file_path) == total_size_in_bytes:
        st.write('[{}_{}] {} already exists, skipping'.format(s, v, name))
    else:
        response = session.get(
            url, stream=True, headers=ADOBE_REQ_HEADERS)
        total_size_in_bytes = int(
            response.headers.get('content-length', 0))
        block_size = 1024  # 1 Kibibyte
        progress_bar = tqdm(total=total_size_in_bytes,
                            unit='iB', unit_scale=True)
        with open(file_path, 'wb') as file:
            for data in response.iter_content(block_size):
                progress_bar.update(len(data))
                file.write(data)
        progress_bar.close()
        if total_size_in_bytes != 0 and progress_bar.n != total_size_in_bytes:
            st.write("ERROR, something went wrong")

# Streamlit UI
st.title("Adobe Offline Package Downloader")

# URL version input
url_version = st.selectbox("Select URL version:", ["v4", "v5", "v6"], index=2)

# Architecture input
architecture = st.selectbox("Select architecture:", ["x86_64", "arm64"])

# Get products button
if st.button("Get Products"):
    selectedVersion = None
    if url_version.lower() == "v4" or url_version == "4":
        selectedVersion = 4
    elif url_version.lower() == "v5" or url_version == "5":
        selectedVersion = 5
    elif url_version.lower() == "v6" or url_version == "6":
        selectedVersion = 6
    else:
        st.write('Invalid URL version selected.')

    ism1 = architecture.lower() in ['arm64', 'arm', 'm1']
    allowedPlatforms = ['macuniversal']
    if ism1:
        allowedPlatforms.append('macarm64')
        st.write('Note: If the Adobe program is NOT listed here, there is no native M1 version.')
        st.write('Use the non-native version with Rosetta 2 until an M1 version is available.')
    else:
        allowedPlatforms.append('osx10-64')
        allowedPlatforms.append('osx10')

    productsPlatform = 'osx10-64,osx10,macarm64,macuniversal'
    adobeurl = ADOBE_PRODUCTS_XML_URL.format(urlVersion=selectedVersion, installPlatform=productsPlatform)

    st.write('Downloading products.xml')
    products_xml = get_products_xml(adobeurl)

    st.write('Parsing products.xml')
    products, cdn = parse_products_xml(products_xml, selectedVersion, allowedPlatforms)

    st.write('CDN: ' + cdn)
    sapCodes = {}
    for p in products.values():
        if not p['hidden']:
            versions = p['versions']
            lastv = None
            for v in reversed(versions.values()):
                if v['buildGuid'] and v['apPlatform'] in allowedPlatforms:
                    lastv = v['productVersion']
            if lastv:
                sapCodes[p['sapCode']] = p['displayName']
    st.write(str(len(sapCodes)) + ' products found:')
    for s, d in sapCodes.items():
        st.write('[{}] {}'.format(s, d))

# Further interaction for product selection, version, language, etc. can be added similarly
