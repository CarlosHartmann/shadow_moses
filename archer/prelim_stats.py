import pandas as pd
import re
import math
import matplotlib.pyplot as plt

THEY_FORMS = ["they", "them", "their", "theirs", "themselves", "themself", "themselfs"]
HE_FORMS = ["he", "him", "his", "himself"]
SHE_FORMS = ["she", "her", "hers", "herself"]
REDDIT_USERNAME_PATTERN = r"\/?u\/[A-Za-z0-9_-]+"
OP_PATTERN = r"\bOP\b"

file =  "first_batch.xlsx"

sheets = ["2010_GPT5mini", "2015_GPT5mini", "2020_GPT5mini"]

data_2010, data_2015, data_2020 = [pd.read_excel(file, sheet_name=sheet) for sheet in sheets]

# only keep rows where column "LLM_response" is not null or empty
data_2010 = data_2010[data_2010["LLM_response"].notnull() & (data_2010["LLM_response"] != "")]
data_2015 = data_2015[data_2015["LLM_response"].notnull() & (data_2015["LLM_response"] != "")]
data_2020 = data_2020[data_2020["LLM_response"].notnull() & (data_2020["LLM_response"] != "")]

# only keep rows where column "gender_info" is "unknown" or "ambiguous"
data_2010 = data_2010[data_2010["gender_info"].isin(["unknown", "ambiguous"])]
data_2015 = data_2015[data_2015["gender_info"].isin(["unknown", "ambiguous"])]
data_2020 = data_2020[data_2020["gender_info"].isin(["unknown", "ambiguous"])]

# only keep rows where column "spacy_filter" is empty
data_2010 = data_2010[data_2010["spacy_filter"].isnull() | (data_2010["spacy_filter"] == "")]
data_2015 = data_2015[data_2015["spacy_filter"].isnull() | (data_2015["spacy_filter"] == "")]
data_2020 = data_2020[data_2020["spacy_filter"].isnull() | (data_2020["spacy_filter"] == "")]

# only keep rows where column "user" is not "AutoModerator"
data_2010 = data_2010[data_2010["user"] != "AutoModerator"]
data_2015 = data_2015[data_2015["user"] != "AutoModerator"]
data_2020 = data_2020[data_2020["user"] != "AutoModerator"]

# each row in "identified_anaphora" is separated by ", "
# count each anaphora that matches any of the forms of they, he, or she, and store the counts in variables "they_count", "he_count", and "she_count" by year
# likewise, if any of the anaphora fit the reddit username pattern, count them in a variable "reddit_username_count" by year
# in the end, return all counts for each year and the % of 'they' forms over all counted anaphora for each year

def count_anaphora(data):
    they_count = 0
    he_count = 0
    she_count = 0
    reddit_username_count = 0
    op_count = 0

    for anaphora_list in data["identified_anaphora"]:

        # as "OP" is almost always included but doesn't count by itself as anaphora, we must remove one entry that fits the OP_Pattern from the list of anaphora if it exists before counting the OP category, which is separate from the other categories of anaphora
        if pd.notnull(anaphora_list) and re.search(OP_PATTERN, anaphora_list, re.IGNORECASE):
            anaphora_list = re.sub(OP_PATTERN, "", anaphora_list, count=1, flags=re.IGNORECASE).strip(", ")

        if pd.isnull(anaphora_list):
            continue
        anaphora_list = anaphora_list.split(", ")
        for anaphora in anaphora_list:
            if anaphora.lower() in THEY_FORMS:
                they_count += 1
            elif anaphora.lower() in HE_FORMS:
                he_count += 1
            elif anaphora.lower() in SHE_FORMS:
                she_count += 1
            elif pd.notnull(anaphora) and re.match(REDDIT_USERNAME_PATTERN, anaphora, re.IGNORECASE):
                reddit_username_count += 1
            elif pd.notnull(anaphora) and re.match(OP_PATTERN, anaphora, re.IGNORECASE):
                op_count += 1

    return they_count, he_count, she_count, reddit_username_count, op_count


def normal_cdf(value):
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def two_proportion_z_test(success_1, total_1, success_2, total_2):
    pooled_proportion = (success_1 + success_2) / (total_1 + total_2)
    standard_error = math.sqrt(
        pooled_proportion
        * (1 - pooled_proportion)
        * ((1 / total_1) + (1 / total_2))
    )
    if standard_error == 0:
        return 0.0, 1.0

    z_statistic = ((success_1 / total_1) - (success_2 / total_2)) / standard_error
    p_value = 2 * (1 - normal_cdf(abs(z_statistic)))
    return z_statistic, p_value


def fisher_exact_two_sided(success_1, total_1, success_2, total_2):
    failures_1 = total_1 - success_1
    failures_2 = total_2 - success_2

    row_1_total = total_1
    row_2_total = total_2
    col_success_total = success_1 + success_2
    grand_total = row_1_total + row_2_total

    def hypergeom_probability(a_value):
        return (
            math.comb(row_1_total, a_value)
            * math.comb(row_2_total, col_success_total - a_value)
            / math.comb(grand_total, col_success_total)
        )

    observed_probability = hypergeom_probability(success_1)
    min_a = max(0, col_success_total - row_2_total)
    max_a = min(row_1_total, col_success_total)

    p_value = 0.0
    for a_value in range(min_a, max_a + 1):
        probability = hypergeom_probability(a_value)
        if probability <= observed_probability + 1e-12:
            p_value += probability

    return min(p_value, 1.0)


def expected_counts(success_1, total_1, success_2, total_2):
    failures_1 = total_1 - success_1
    failures_2 = total_2 - success_2
    grand_total = total_1 + total_2
    success_total = success_1 + success_2
    failure_total = failures_1 + failures_2

    return [
        total_1 * success_total / grand_total,
        total_1 * failure_total / grand_total,
        total_2 * success_total / grand_total,
        total_2 * failure_total / grand_total,
    ]

they_count_2010, he_count_2010, she_count_2010, reddit_username_count_2010, op_count_2010 = count_anaphora(data_2010)
they_count_2015, he_count_2015, she_count_2015, reddit_username_count_2015, op_count_2015 = count_anaphora(data_2015)
they_count_2020, he_count_2020, she_count_2020, reddit_username_count_2020, op_count_2020 = count_anaphora(data_2020)

total_anaphora_2010 = they_count_2010 + he_count_2010 + she_count_2010 + reddit_username_count_2010 + op_count_2010
total_anaphora_2015 = they_count_2015 + he_count_2015 + she_count_2015 + reddit_username_count_2015 + op_count_2015
total_anaphora_2020 = they_count_2020 + he_count_2020 + she_count_2020 + reddit_username_count_2020 + op_count_2020

they_percentage_2010 = (they_count_2010 / total_anaphora_2010) * 100 if total_anaphora_2010 > 0 else 0
they_percentage_2015 = (they_count_2015 / total_anaphora_2015) * 100 if total_anaphora_2015 > 0 else 0
they_percentage_2020 = (they_count_2020 / total_anaphora_2020) * 100 if total_anaphora_2020 > 0 else 0

print(f"2010: They count: {they_count_2010}, He count: {he_count_2010}, She count: {she_count_2010}, Reddit username count: {reddit_username_count_2010}, OP count: {op_count_2010}, They percentage: {they_percentage_2010:.2f}%")
print(f"2015: They count: {they_count_2015}, He count: {he_count_2015}, She count: {she_count_2015}, Reddit username count: {reddit_username_count_2015}, OP count: {op_count_2015}, They percentage: {they_percentage_2015:.2f}%")
print(f"2020: They count: {they_count_2020}, He count: {he_count_2020}, She count: {she_count_2020}, Reddit username count: {reddit_username_count_2020}, OP count: {op_count_2020}, They percentage: {they_percentage_2020:.2f}%")

print("\n2015 vs 2020 significance test for 'they' usage")
expected = expected_counts(they_count_2015, total_anaphora_2015, they_count_2020, total_anaphora_2020)

if min(expected) < 5:
    p_value = fisher_exact_two_sided(they_count_2015, total_anaphora_2015, they_count_2020, total_anaphora_2020)
    test_name = "Fisher's exact test (two-sided)"
    statistic_text = "Odds-ratio-style exact comparison"
else:
    z_statistic, p_value = two_proportion_z_test(
        they_count_2015,
        total_anaphora_2015,
        they_count_2020,
        total_anaphora_2020,
    )
    test_name = "Two-proportion z-test (two-sided)"
    statistic_text = f"z = {z_statistic:.4f}"

alpha = 0.05
result_text = "statistically significant" if p_value < alpha else "not statistically significant"

print(f"Test used: {test_name}")
print(f"Statistic: {statistic_text}")
print(f"p-value: {p_value:.6g}")
print(f"At alpha={alpha}, the 2015 vs 2020 difference in 'they' use is {result_text}.")

# Generate a stacked bar graph of the counts of 'they', 'he', 'she', reddit usernames, and OP for each year
# one bar per year, with different colors for each category of anaphora

categories = ["they", "he", "she", "Reddit Username", "OP"]
counts_2010 = [they_count_2010, he_count_2010, she_count_2010, reddit_username_count_2010, op_count_2010]
counts_2015 = [they_count_2015, he_count_2015, she_count_2015, reddit_username_count_2015, op_count_2015]
counts_2020 = [they_count_2020, he_count_2020, she_count_2020, reddit_username_count_2020, op_count_2020]

years = ["2010", "2015", "2020"]
x = [i * 1.5 for i in range(len(years))]
bar_width = 1.2

counts_by_category = {
    "they": [they_count_2010, they_count_2015, they_count_2020],
    "he": [he_count_2010, he_count_2015, he_count_2020],
    "she": [she_count_2010, she_count_2015, she_count_2020],
    "Reddit Username": [reddit_username_count_2010, reddit_username_count_2015, reddit_username_count_2020],
    "OP": [op_count_2010, op_count_2015, op_count_2020],
}

totals_by_year = [total_anaphora_2010, total_anaphora_2015, total_anaphora_2020]

bottom = [0] * len(years)
for category in categories:
    raw_values = counts_by_category[category]
    values = [
        (raw_values[i] / totals_by_year[i]) * 100 if totals_by_year[i] > 0 else 0
        for i in range(len(years))
    ]
    plt.bar(x, values, width=bar_width, bottom=bottom, label=category)
    bottom = [bottom[i] + values[i] for i in range(len(years))]

plt.xticks(x, years)
plt.xlabel("Year")
plt.ylabel("Proportion (%)")
plt.ylim(0, 100)
plt.title("Anaphora Category Proportions by Year")
legend = plt.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0.0)
for legend_text in legend.get_texts():
    if legend_text.get_text() != "Reddit Username":
        legend_text.set_fontstyle("italic")
plt.tight_layout(rect=[0, 0, 0.8, 0.75])

# save as tiff
plt.savefig("anaphora_counts_by_year.tiff", format="tiff", dpi=300)