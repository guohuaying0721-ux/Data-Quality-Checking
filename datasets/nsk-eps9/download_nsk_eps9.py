import json, os, requests

# ============================================================
# 数据集下载配置 - NSK-EPS9
# ============================================================

# DS-202606-0067 验证
manifest_0067 = r"C:\Users\20883\.claude\projects\C--Users-20883\94169ea9-2303-45ec-9bc4-13a53ecb1a5f\tool-results\tool_xTR2HtdTbbaQLYSsCzmgp4g6.txt"
output_dir_0067 = r"E:\Data-Quality-Checking\NSK-EPS9数据集\DS-202606-0067_验证"

# DS-202606-0066 基准
manifest_0066 = r"C:\Users\20883\.claude\projects\C--Users-20883\94169ea9-2303-45ec-9bc4-13a53ecb1a5f\tool-results\tool_IcpziS8JByoADgaCNHOeVuiA.txt"
output_dir_0066 = r"E:\Data-Quality-Checking\NSK-EPS9数据集\DS-202606-0066_基准"

# 产品信息映射（NSK-EPS9 工位）
PRODUCTS_INFO = {
    "768b784b6c5e4fe0ae9c00c455d0151f": {
        "productNumber": "GEN3",
        "productName": "GEN3"
    },
    "8c20f89b8a7e401cb1141d6d26e29a8f": {
        "productNumber": "MQB",
        "productName": "MQB"
    }
}

def download_dataset(manifest_file, output_dir):
    """下载单个数据集"""
    with open(manifest_file, 'r', encoding='utf-8') as f:
        m = json.load(f)

    print(f"数据集: {m['datasetCode']} - {m['datasetName']}")
    print(f"总文件数: {m['total']}")
    print(f"下载目录: {output_dir}")
    print("="*60)

    os.makedirs(output_dir, exist_ok=True)

    downloaded = 0
    failed = 0
    skipped = 0

    for item in m['files']:
        wav_name = item['originalWav']
        json_name = wav_name.replace('.wav', '.json')
        wav_path = os.path.join(output_dir, wav_name)
        json_path = os.path.join(output_dir, json_name)

        # 下载WAV
        if not os.path.exists(wav_path):
            try:
                with requests.get(item['sourceWavUrl'], stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(wav_path, 'wb') as f:
                        for chunk in r.iter_content(64*1024):
                            f.write(chunk)
                downloaded += 1
                print(f"[OK] {wav_name}")
            except Exception as e:
                failed += 1
                print(f"[FAIL] {wav_name}: {e}")
        else:
            skipped += 1
            print(f"[SKIP] {wav_name} 已存在")

        # 生成JSON元数据（包含产品信息）
        if not os.path.exists(json_path):
            metadata = {
                "datasetCode": m['datasetCode'],
                "datasetName": m['datasetName'],
                "version": m['version'],
                "fileID": item['fileID'],
                "originalWav": item['originalWav'],
                "dateDir": item['dateDir'],
                "channelID": item['channelID'],
                "productUniqueCode": item['productUniqueCode'],
                "productInfo": PRODUCTS_INFO.get(item.get('productID', ''), {}),
                "detectPointName": item['detectPointName'],
                "channel": item['channel'],
                "stage": item['stage'],
                "channelCount": item['channelCount']
            }
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

    print("="*60)
    print(f"下载完成: 成功 {downloaded}, 失败 {failed}, 跳过 {skipped}")
    print()

# ============================================================
# 执行下载
# ============================================================
if __name__ == "__main__":
    print("开始下载 NSK-EPS9 数据集...")
    print()

    # 下载 DS-202606-0067 验证
    download_dataset(manifest_0067, output_dir_0067)

    # 下载 DS-202606-0066 基准
    download_dataset(manifest_0066, output_dir_0066)

    print("全部下载完成！")
