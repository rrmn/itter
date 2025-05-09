#!/bin/bash

# Define the output file
OUTPUT_FILE="fastapi_project_files_with_contents.txt"

# Start fresh by clearing the output file if it exists
rm -f "$OUTPUT_FILE"

# Get the full path of the current directory
PROJECT_DIR=$(pwd)

# Add a GPT-friendly Markdown header
echo -e "# FastAPI Project File List with Contents\n" > "$OUTPUT_FILE"

# Find all matching files and count them for progress tracking
FILES=($(find "$PROJECT_DIR" -type f \( -name "*.py" -o -name "*.html" \) \
  ! -path "*/node_modules/*" \
  ! -path "*/.venv/*" \
  ! -path "*/.*" \
  ! -path "*/__pycache__/*"))
TOTAL_FILES=${#FILES[@]}

# Initialize progress counter
echo "Processing $TOTAL_FILES files..."
COUNT=0

# Process each file
for file in "${FILES[@]}"; do
  COUNT=$((COUNT + 1))
  
  # Add file path as a Markdown header
  echo -e "## File: \`$file\`\n" >> "$OUTPUT_FILE"

  # Append the contents of the file, wrapped in a Markdown code block
  echo -e '```' >> "$OUTPUT_FILE"
  cat "$file" >> "$OUTPUT_FILE"
  echo -e '```\n' >> "$OUTPUT_FILE"

  # Show progress
  printf "\rProgress: %d/%d files processed" "$COUNT" "$TOTAL_FILES"
done

# Print a newline after the progress indicator
echo

# Final message
echo "File list with contents saved to $OUTPUT_FILE"
