import json, os, requests

# ============================================================
# 数据集下载配置
# ============================================================

# DS-202606-0063 验证
manifest_0063 = r"C:\Users\20883\.claude\projects\C--Users-20883\94169ea9-2303-45ec-9bc4-13a53ecb1a5f\tool-results\tool_qtkBDe2EOwUmnALgpR32LI0c.txt"
output_dir_0063 = r"E:\Data-Quality-Checking\NSK-EPS8数据集\DS-202606-0063_验证"

# DS-202606-0062 基准
manifest_0062 = r"C:\Users\20883\.claude\projects\C--Users-20883\94169ea9-2303-45ec-9bc4-13a53ecb1a5f\tool-results\tool_wfXvlaRHFOwNb9W9rjuvQeK4.txt"
output_dir_0062 = r"E:\Data-Quality-Checking\NSK-EPS8数据集\DS-202606-0062_基准"

# 产品信息映射（NSK-EPS8 工位）
PRODUCTS_INFO = {
    "f8cf27042d29428bbf558362a1092e50": {
        "productNumber": "MQBddd",
        "productName": "MQBddd"
    },
    "fe70c7cdaea0496bba53620d89f76ae3": {
        "productNumber": "MQB",
        "productName": "MQB"
    },
    "e7e6c7b599c64137af830105d893a48f": {
        "productNumber": "TestProd",
        "productName": "eps8"
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
    print("开始下载 NSK-EPS8 数据集...")
    print()

    # 下载 DS-202606-0063 验证
    download_dataset(manifest_0063, output_dir_0063)

    # 下载 DS-202606-0062 基准
    download_dataset(manifest_0062, output_dir_0062)

    print("全部下载完成！")
