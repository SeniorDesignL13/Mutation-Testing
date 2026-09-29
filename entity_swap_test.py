import spacy

nlp = spacy.load("en_core_web_sm")

# A small pool of replacement places to swap in
PLACE_POOL = ["Madrid", "Berlin", "Rome", "Vienna", "Warsaw"]


def entity_swap(text, swap_word="Madrid"):
    doc = nlp(text)
    new_text = text

    for ent in doc.ents:
        if ent.label_ == "GPE":  # GPE = place (city/country)
            new_text = new_text.replace(ent.text, swap_word)
            print(f"Swapped '{ent.text}' -> '{swap_word}'")
            break  # only swap the first place found, for now

    return new_text


original = "The treaty was signed in Lisbon in 2007."
mutated = entity_swap(original)

print("Original:", original)
print("Mutant:  ", mutated)
