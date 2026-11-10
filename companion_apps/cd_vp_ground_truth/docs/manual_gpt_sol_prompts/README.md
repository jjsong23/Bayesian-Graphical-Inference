# Manual GPT-6 Sol evidence-curation prompts

Open one fresh GPT-6 Sol chat for each text file in this directory and paste the
entire file as the first message. Enable web search. The four chats correspond
to the four active evidence-validation profiles:

1. `01_physical_interaction.txt`
2. `02_directed_phosphorylation.txt`
3. `03_acute_regulatory_signaling.txt`
4. `04_messenger_binding.txt`

The former generic functional-association profile has been retired. Acute
regulatory signaling now calibrates both OmniPath and STRING. Directed
phosphorylation remains a stricter profile, and every phosphorylation candidate
must also be included in the acute-regulatory candidate set.

Each chat is instructed to return tab-separated tables with consistent columns.
Save each final answer as UTF-8 text or TSV so it can be imported later.
