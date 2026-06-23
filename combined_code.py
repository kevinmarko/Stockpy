import os

def combine_python_files(output_filename="combined_code.txt"):
    """
    Combines all .py files in the current directory into a single text file
    with clear headers for NotebookLM to understand the context.
    """
    # Get all .py files in the current directory
    py_files = [f for f in os.listdir('.') if f.endswith('.py') and f != output_filename]
    
    # Sort files alphabetically so the output is consistent
    py_files.sort()
    
    with open(output_filename, 'w', encoding='utf-8') as outfile:
        for filename in py_files:
            print(f"Adding {filename}...")
            
            # Write a header for context
            outfile.write(f"\n{'='*20}\n")
            outfile.write(f"FILE: {filename}\n")
            outfile.write(f"{'='*20}\n\n")
            
            # Read and append the file content
            with open(filename, 'r', encoding='utf-8') as infile:
                outfile.write(infile.read())
            
            # Ensure there's a space between files
            outfile.write("\n")
            
    print(f"\nSuccess! All files combined into '{output_filename}'.")

if __name__ == "__main__":
    combine_python_files()