from agents.parser_agent import ParserAgent


print("PARSER_AGENT_STARTED")

parser = ParserAgent()

markdown_path = parser.run(
    pdf_path="data/input/article.pdf",
    output_dir="data/output"
)

print("PARSER_RESULT:")
print(markdown_path)

