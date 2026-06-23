Работай с проектом в папке C:\Project.

Нужно выполнить учебный multi-agent workflow средствами Hermes.

Цель:
создать двух субагентов и запустить их последовательную оркестрацию для обработки научной PDF-статьи и последующего перевода.

Обязательно используй tool delegate_task для создания и запуска субагентов.

SubAgent-1: MinerU PDF Parser
Goal:
Проверить наличие PDF-файла C:\Project\data\input\article.pdf и запустить парсинг через файл C:\Project\run_parser_agent.py.

Context:
Проект находится в C:\Project.
Команда запуска:
cd C:\Project
.\venv\Scripts\python.exe run_parser_agent.py

Результат Agent-1 должен быть Markdown-файл:
C:\Project\data\output\final\article.md
или распарсенный Markdown из папки:
C:\Project\data\output\article\auto\article.md

SubAgent-2: Markdown Translator
Goal:
Получить Markdown, созданный первым агентом, и получить русский перевод статьи.

Context:
Проект находится в C:\Project.
Команда запуска:
cd C:\Project
.\venv\Scripts\python.exe run_translator_agent.py

Оркестрация:
1. Через delegate_task запусти SubAgent-1.
2. Дождись результата SubAgent-1.
3. Через delegate_task запусти SubAgent-2 и передай ему путь к Markdown.
4. Проверь наличие файлов:
C:\Project\data\output\final\article.md
C:\Project\data\output\final\translated_article.md
5. В финальном ответе выведи:
- статус SubAgent-1
- статус SubAgent-2
- путь к Markdown
- путь к русскому переводу
- вывод, что Hermes orchestration workflow завершён успешно.
