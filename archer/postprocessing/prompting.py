'''
prompting module: A module to run the linguistic analysis prompting.
Utilizes OpenRouter, therefore requires OPENROUTER_API_KEY environment variable to be set with a valid OpenRouter API key.
Processes the data and saves the LLM response in a new column, exporting to a new CSV file.
'''

import os
import pandas as pd
import requests

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "openai/gpt-5-mini"
CURRENT_PROMPTFILE = "A"

def openrouter_request(prompt: str, system_message: str, model: str) -> str:

    if system_message == "":
        raise ValueError("System message cannot be empty for OpenRouter requests.")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
    "model": model,
    "messages": [
    {
    "role": "system",
    "content": system_message
    },
    {
    "role": "user",
    "content": prompt
    }
    ],
    "temperature": 0
    }

    if "terminus" in model:
        # add boolean extra_body={"reasoning": {"effort": "high"} for terminus models
        payload["extra_body"] = {"reasoning": {"effort": "high"}}

    response = requests.post(url, headers=headers, json=payload)
    response_json = response.json()

    if response.status_code != 200:
        raise Exception(f"OpenRouter API request failed with status code {response.status_code}: {response_json}")
    
    return response_json['choices'][0]['message']['content']

def main():
    data_dirs = ['2010', '2015', '2020']
    for data_dir in data_dirs:
        print(f"Processing data directory: {data_dir}")
        current_path = os.path.dirname(os.path.realpath(__file__))
        data_dir = os.path.join(current_path, '..', data_dir)
        files = [elem for elem in os.listdir(data_dir) if elem.endswith('.csv')]
        assert len(files) == 4, f"Expected four CSV files in {data_dir}, found {len(files)}"
        # choose the one that starts with "non_flagged"
        file_path = os.path.join(data_dir, [f for f in files if f.startswith("non_flagged")][0])

        df = pd.read_csv(file_path, index_col=False)
        
        promptfile = os.path.join(current_path, '..', 'prompting', f'{CURRENT_PROMPTFILE}.txt')
        systemmessagefile = os.path.join(current_path, '..', 'prompting', 'system_message.txt')
        with open(promptfile, 'r') as f:
            prompt_template = f.read()
        with open(systemmessagefile, 'r') as f:
            system_message = f.read()

        for i, row in df.iterrows():
            if i % 10 == 0:
                print(f"Processing comment {i+1}/{len(df)}")
            
            comment = row["text"]
            subreddit = row["subreddit"]
            year = row["year"]
            month = row["month"]
            permalink = row["permalink"]

            prompt = prompt_template.replace("{{SUBREDDIT}}", subreddit).replace("{{YEAR}}", str(year)).replace("{{MONTH}}", str(month)).replace("{{PERMALINK}}", permalink).replace("{{COMMENT}}", comment)
            responses = []
            try:
                response = openrouter_request(prompt, system_message, MODEL)
                responses.append(response)

                # the response will begin with either "gender ambiguous", "gender unknown", or "gender known"
                # extract this information and save it in a new column "gender_info"
                if response.startswith("gender ambiguous"):
                    gender_info = "ambiguous"
                elif response.startswith("gender unknown"):
                    gender_info = "unknown"
                elif response.startswith("gender known"):
                    gender_info = "known"
                else:
                    gender_info = "unknown"
                
                df.at[i, "gender_info"] = gender_info

                # the response will end like so: "ANAPHORA: he, he, his, OP".
                # extract the anaphora and save it in a new column "identified_anaphora"
                if "ANAPHORA:" in response:
                    identified_anaphora = response.split("ANAPHORA:")[1].strip()
                else:
                    identified_anaphora = ""

                df.at[i, "identified_anaphora"] = identified_anaphora

                df.at[i, "LLM_response"] = response

            except Exception as e:
                print(f"Error processing comment: {comment}. Error: {e}")
                responses.append(None)  # Append None for comments that couldn't be processed
            
            if i % 10 == 0:
                print(f"Saving progress after processing comment {i+1}/{len(df)}")
                output_file_path = os.path.join(data_dir, f"LLM_analyzed_{file_path.split('/')[-1]}")
                df.to_csv(output_file_path, index=False)

            # stop after 500 comments
            if i >= 499:
                print("Reached 500 comments, stopping to avoid excessive API calls.")
                break

        
        output_file_path = os.path.join(data_dir, f"LLM_analyzed_{file_path.split('/')[-1]}")
        df.to_csv(output_file_path, index=False)

if __name__ == "__main__":
    main()