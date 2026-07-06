'''
openai_moderation: A module for applying OpenAI's content moderation API to filter out comments that violate content policies.
This module defines a function `apply_openai_moderation` that takes a DataFrame of comments and applies the OpenAI moderation API to determine if any comments should be flagged for containing inappropriate content.
The results are removed from the file and stored separately.
Requires OPENAI_API_KEY environment variable to be set with a valid OpenAI API key.
'''

import os
import pandas as pd
from openai import OpenAI


def apply_openai_moderation(df, text_column="text"):
    """
    Applies OpenAI's content moderation API to filter out comments that violate content policies.
    
    Parameters:
    - df: A pandas DataFrame containing the comments to be moderated.
    - text_column: The name of the column in the DataFrame that contains the comment text (default is "text").
    
    Returns:
    - A DataFrame with an additional column "moderation_flag" indicating whether each comment was flagged by the moderation API.
    """
    if "OPENAI_API_KEY" not in os.environ:
        raise EnvironmentError("OPENAI_API_KEY environment variable not set. Please set it to a valid OpenAI API key.")
    
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    
    moderation_flags = []
    
    for comment in df[text_column]:
        try:
            response = client.moderations.create(input=comment)
            flagged = response.results[0].flagged
            moderation_flags.append(flagged)
        except Exception as e:
            print(f"Error processing comment: {comment}. Error: {e}")
            moderation_flags.append(None)  # Append None for comments that couldn't be processed
    
    df["moderation_flag"] = moderation_flags
    return df


def main():
    data_dirs = ['2016', '2017', '2018', '2019']
    for data_dir in data_dirs:
        current_path = os.path.dirname(os.path.realpath(__file__))
        data_dir = os.path.join(current_path, '..', data_dir)
        files = [elem for elem in os.listdir(data_dir) if elem.endswith('.csv')]
        assert len(files) == 2, f"Expected two CSV files in {data_dir}, found {len(files)}"
        # choose the one that starts with "spacy_filtered"
        file_path = os.path.join(data_dir, [f for f in files if f.startswith("spacy_filtered")][0])

        df = pd.read_csv(file_path, index_col=False)
        
        moderated_df = apply_openai_moderation(df)

        # save flagged comments separately
        moderated_df[moderated_df["moderation_flag"] == True].to_csv(os.path.join(data_dir, f"flagged_{file_path.split('/')[-1]}"), index=False)
        # save non-flagged comments separately
        moderated_df[moderated_df["moderation_flag"] == False].to_csv(os.path.join(data_dir, f"non_flagged_{file_path.split('/')[-1]}"), index=False)


if __name__ == "__main__":
    main()