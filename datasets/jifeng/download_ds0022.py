import json, os, requests

# DS-202606-0022 继峰验证集2 (40号机)
manifest_file = r"C:\Users\20883\.claude\projects\C--Users-20883\a1c71450-d840-45d4-90f0-dc3144f6b5e5\tool-results\mcp-plugin_smart-tpm-prod_smart-tpm-datasets_export_files_manifest-1781082795347.txt"
output_dir = r"E:\继峰数据集\DS-202606-0022_继峰验证集2"

with open(manifest_file, 'r', encoding='utf-8') as f:
    m = json.load(f)

print(f"数据集: {m['datasetCode']} - {m['datasetName']}")
print(f"总文件数: {m['total']}")
print(f"下载目录: {output_dir}")
print("="*60)

downloaded = 0
failed = 0

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
        print(f"[SKIP] {wav_name} 已存在")

    # 生成JSON元数据
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
            "detectPointName": item['detectPointName'],
            "channel": item['channel'],
            "stage": item['stage'],
            "channelCount": item['channelCount']
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

print("="*60)
print(f"下载完成: 成功 {downloaded}, 失败 {failed}, 跳过 {m['total'] - downloaded - failed}")
