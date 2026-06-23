import subprocess
import logging
from pathlib import Path


logging.basicConfig(level=logging.INFO)


class ParserAgent:

    def run(self, pdf_path, output_dir):

        try:

            logging.info("Parsing started")

            command = [
                "mineru",
                "-p",
                pdf_path,
                "-o",
                output_dir,
                "-b",
                "pipeline"
            ]
            subprocess.run(command, check=True)
            logging.info("Parsing finished")
            output_path = Path(output_dir)
            md_files = list(output_path.rglob("*.md"))

            if not md_files:
                raise FileNotFoundError("Markdown file not found")

            markdown_path = md_files[0]
            logging.info(f"Markdown found: {markdown_path}")
            return markdown_path

        except subprocess.CalledProcessError as e:

            logging.error(f"Parsing error: {e}")

        except Exception as e:

            logging.error(f"Unexpected error: {e}")


            