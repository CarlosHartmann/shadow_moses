import os
import spacy
import pandas as pd

nlp = spacy.load("en_core_web_trf")


def check_span(span):  # span has format e.g. "(0, 4)"
    span = span.strip("()")
    start, end = map(int, span.split(", "))
    return start, end

def main():
    data_dirs = ['2016', '2017', '2018', '2019']
    for data_dir in data_dirs:
        current_path = os.path.dirname(os.path.realpath(__file__))
        data_dir = os.path.join(current_path, '..', data_dir)
        file = [elem for elem in os.listdir(data_dir) if elem.endswith('.csv')]
        assert len(file) == 1, f"Expected one CSV file in {data_dir}, found {len(file)}"
        file_path = os.path.join(data_dir, file[0])

        df = pd.read_csv(file_path, sep=";", index_col=False)
        
        text, span = df["text"], df["span"]

        spacy_filter = []
        for comment, span in zip(text, span):
            doc = nlp(comment)

            # test whether the character span points to a NOUN token
            start, end = check_span(span)
            if start >= end or start < 0:
                raise ValueError(f"Invalid span: {span} in comment: {comment}")

            token_span = doc.char_span(start, end, alignment_mode="expand")
            if token_span is None or len(token_span) == 0:
                raise ValueError(f"Invalid token span: {span} in comment: {comment}")

            if token_span.root.pos_ in ["NOUN", "PROPN"]:
                spacy_filter.append("")
            else:
                #print(f"Filtered out a comment where OP had the tag: {token_span.root.pos_}")
                spacy_filter.append("OP not a noun")

        df["spacy_filter"] = spacy_filter

        output_file_path = os.path.join(data_dir, f"spacy_filtered_{file[0]}")
        df.to_csv(output_file_path, index=False)


if __name__ == "__main__":
    main()