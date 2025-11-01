import os
import pandas as pd
from zipfile import BadZipFile

# Set the folder path where your .xlsx files are stored
input_folder = r"C:\Users\user\Desktop\BotResults\results\Forward Test\live"
output_folder = r"C:\Users\user\projects\PerplexityCombinedTest\csvResults"

# Make sure output folder exists
os.makedirs(output_folder, exist_ok=True)

# Track results
converted_count = 0
failed_files = []

# Loop through all files in the input folder
for file in os.listdir(input_folder):
    if file.endswith(".xlsx"):
        file_path = os.path.join(input_folder, file)
        
        try:
            # Read the Excel file
            df = pd.read_excel(file_path)
            
            # Convert to .csv with the same filename
            csv_filename = os.path.splitext(file)[0] + ".csv"
            csv_path = os.path.join(output_folder, csv_filename)
            
            # Save as CSV (without index column)
            df.to_csv(csv_path, index=False, encoding="utf-8")
            
            print(f"✓ Converted: {file} -> {csv_filename}")
            converted_count += 1
            
        except BadZipFile as e:
            print(f"✗ SKIPPED (corrupted file): {file}")
            failed_files.append((file, "Corrupted Excel file"))
        except Exception as e:
            print(f"✗ FAILED: {file} - {type(e).__name__}: {str(e)}")
            failed_files.append((file, str(e)))

# Summary
print("\n" + "=" * 60)
print(f"Conversion complete: {converted_count} files converted")
if failed_files:
    print(f"\n{len(failed_files)} file(s) failed:")
    for filename, error in failed_files:
        print(f"  - {filename}: {error}")
print("=" * 60)
