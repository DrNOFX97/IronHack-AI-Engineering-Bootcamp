import streamlit as st
import os
import platform
import locale
import requests
import json
import random
import string
from collections import OrderedDict
from xml.etree import ElementTree as ET
from subprocess import Popen, PIPE
from tqdm.auto import tqdm

session = requests.sessions.Session()

VERSION_STR = '0.2.0'
ADOBE_PRODUCTS_XML_URL = 'https://prod-rel-ffc-ccm.oobesaas.adobe.com/adobe-ffc-external/core/v{urlVersion}/products/all?_type=xml&channel=ccm&channel=sti&platform={installPlatform}&productType=Desktop'
ADOBE_APPLICATION_JSON_URL = 'https://cdn-ffc.oobesaas.adobe.com/core/v3/applications'
ADOBE_REQ_HEADERS = {
    'X-Adobe-App-Id': 'accc-apps-panel-desktop',
    'User-Agent': 'Adobe Application Manager 2.0',
    'X-Api-Key': 'CC_HD_ESD_1_0',
    'Cookie': 'fg=' + ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(26)) + '======'
}
ADOBE_DL_HEADERS = {
    'User-Agent': 'Creative Cloud'
}

def r(url, headers=ADOBE_REQ_HEADERS):
    req = session.get(url, headers=headers)
    req.encoding = 'utf-8'
    return req.text

def get_products_xml(adobeurl):
    return ET.fromstring(r(adobeurl))

def parse_products_xml(products_xml, urlVersion, allowedPlatforms):
    prefix = 'channels/' if urlVersion == 6 else ''
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
                    break

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

            products[sap]['versions'][productVersion] = {
                'sapCode': sap,
                'baseVersion': baseVersion,
                'productVersion': productVersion,
                'apPlatform': appplatform,
                'dependencies': [{'sapCode': d.find('sapCode').text, 'version': d.find('baseVersion').text} for d in dependencies],
                'buildGuid': buildGuid
            }
    return products, cdn

def get_application_json(buildGuid):
    headers = ADOBE_REQ_HEADERS.copy()
    headers['x-adobe-build-guid'] = buildGuid
    return json.loads(r(ADOBE_APPLICATION_JSON_URL, headers))

def download_file(url, product_dir, s, v, name=None):
    if not name:
        name = url.split('/')[-1].split('?')[0]
    file_path = os.path.join(product_dir, name)
    response = session.head(url, stream=True, headers=ADOBE_DL_HEADERS)
    total_size_in_bytes = int(response.headers.get('content-length', 0))
    if os.path.isfile(file_path) and os.path.getsize(file_path) == total_size_in_bytes:
        st.write(f'[{s}_{v}] {name} already exists, skipping')
    else:
        response = session.get(url, stream=True, headers=ADOBE_REQ_HEADERS)
        total_size_in_bytes = int(response.headers.get('content-length', 0))
        block_size = 1024
        progress_bar = tqdm(total=total_size_in_bytes, unit='iB', unit_scale=True)
        with open(file_path, 'wb') as file:
            for data in response.iter_content(block_size):
                progress_bar.update(len(data))
                file.write(data)
        progress_bar.close()
        if total_size_in_bytes != 0 and progress_bar.n != total_size_in_bytes:
            st.write("ERROR, something went wrong")

def get_download_path():
    dest = st.text_input("Enter download path:")
    if not dest:
        st.stop()
    return dest

def run_downloader():
    st.title("Adobe Offline Package Downloader")
    st.write(f"Version: {VERSION_STR}")

    products, cdn, sapCodes, allowedPlatforms = get_products()

    sapCode = st.selectbox("Select SAP Code:", options=[""] + list(sapCodes.keys()))
    if not sapCode:
        st.stop()

    product = products.get(sapCode)
    versions = product['versions']
    version = st.selectbox("Select Version:", options=[""] + list(versions.keys()))
    if not version:
        st.stop()

    installLanguage = st.selectbox("Select Install Language:", options=['en_US', 'en_GB', 'en_IL', 'en_AE', 'es_ES', 'es_MX', 'pt_BR', 'fr_FR', 'fr_CA', 'fr_MA', 'it_IT', 'de_DE', 'nl_NL', 'ru_RU', 'uk_UA', 'zh_TW', 'zh_CN', 'ja_JP', 'ko_KR', 'pl_PL', 'hu_HU', 'cs_CZ', 'tr_TR', 'sv_SE', 'nb_NO', 'fi_FI', 'da_DK', 'ALL'])
    dest = get_download_path()

    st.write(f"Selected SAP Code: {sapCode}")
    st.write(f"Selected Version: {version}")
    st.write(f"Selected Install Language: {installLanguage}")
    st.write(f"Download Path: {dest}")

    if st.button("Start Download"):
        prodInfo = versions[version]
        prods_to_download = []
        dependencies = prodInfo['dependencies']
        for d in dependencies:
            firstArch = firstGuid = buildGuid = None
            for p in products[d['sapCode']]['versions']:
                if products[d['sapCode']]['versions'][p]['baseVersion'] == d['version']:
                    if not firstGuid:
                        firstGuid = products[d['sapCode']]['versions'][p]['buildGuid']
                        firstArch = products[d['sapCode']]['versions'][p]['apPlatform']
                    if products[d['sapCode']]['versions'][p]['apPlatform'] in allowedPlatforms:
                        buildGuid = products[d['sapCode']]['versions'][p]['buildGuid']
                        break
            if not buildGuid:
                buildGuid = firstGuid
            prods_to_download.append({'sapCode': d['sapCode'], 'buildGuid': buildGuid})

        for prod in prods_to_download:
            appInfo = get_application_json(prod['buildGuid'])
            downloadURL = appInfo['packages'][0]['package_url']
            download_file(downloadURL, dest, prod['sapCode'], version)

if __name__ == "__main__":
    run_downloader()
