"""
contains all data prep functions of virHEAT
"""

# BUILT-INS
import os
import re
import pathlib
import sys

# LIBS
import numpy as np
import pandas as pd


def get_files(path, type):
    """
    returns a list of vcf files in a paticular folder
    """
    return list(pathlib.Path(path).glob(f'*.{type}'))


def get_digit_and_alpha(filename):
    """
    get digits and alpha in file names
    """
    digit_match = re.search(r'\d+', filename)
    if digit_match:
        digit = digit_match.group()
        alpha = re.sub(r'\d+', '', filename)
    else:
        digit = ''
        alpha = filename
    return (alpha, int(digit) if digit else float('inf'))


def convert_string(string):
    """
    converts string to its right type
    """
    string = string.replace("\n", "")
    if string.isdecimal():
        return(int(string))
    elif string.replace('.', '', 1).isdecimal():
        return(float(string))
    else:
        return string


def read_vcf(vcf_file, reference):
    """
    parse vcf files to dictionary
    """
    vcf_dict = {}

    # get header and values
    with open(vcf_file, "r") as f:
        header_lines = [l.strip().split("\t") for l in f if l.startswith('#CHROM')]
        if not header_lines:
            print(f"\033[31m\033[1mWARNING:\033[0m {vcf_file} does not contain a '#CHROM' header!")
            return {}
        header = header_lines[0]
    # get each line as frequency_lists
    with open(vcf_file, "r") as f:
        lines = [l.strip().split("\t") for l in f if l.startswith(reference)]
    # check if vcf is empty
    if not lines:
        print(f"\033[31m\033[1mWARNING:\033[0m {vcf_file} has no variants to {reference}!")
    # get standard headers as keys
    for key in header[0:6]:
        vcf_dict[key] = []
    # functional effect
    vcf_dict["MUT_TYPE_"] = []
    # info field
    for line in lines:
        for info in line[7].split(";"):
            if "=" in info:
                vcf_dict[info.split("=")[0]] = []
    # fill in dictionary
    for line in lines:
        # remember keys that have an entry already
        visited_keys = []
        # check if there are multiple called variants at a single position
        # separated by a comma
        length_variants = len(line[4].split(','))
        for idx, key in enumerate(header[0:6]):
            sublines = line[idx].split(',')
            for i in range(length_variants):
                try:
                    vcf_dict[key].append(convert_string(sublines[i]))
                except IndexError:
                    vcf_dict[key].append(convert_string(sublines[0]))
        # get mutation type
        mutations = line[4].split(',')
        for mutation in mutations:
            if len(line[3]) == len(mutation):
                vcf_dict["MUT_TYPE_"].append("SNV")
            elif len(line[3]) < len(mutation):
                vcf_dict["MUT_TYPE_"].append("INS")
            elif len(line[3]) > len(mutation):
                vcf_dict["MUT_TYPE_"].append("DEL")
        visited_keys.extend(header[0:6])
        visited_keys.append("MUT_TYPE_")
        # get data from info field
        for info in line[7].split(";"):
            if "=" in info:
                key, val = info.split("=")
                val_list = val.split(',')
                for value in val_list:
                    vcf_dict[key].append(convert_string(value))
                visited_keys.append(key)
        # append none for each none visited key in the INFO field
        for key in [k for k in vcf_dict.keys() if k not in visited_keys]:
            vcf_dict[key].extend([None]*length_variants)

    return vcf_dict


def extract_vcf_data(vcf_files, reference, threshold=0, scores=False):
    """
    extract relevant vcf data
    """

    file_names = []
    frequency_lists = []

    for file in vcf_files:
        file_names.append(os.path.splitext(os.path.basename(file))[0])
        vcf_dict = read_vcf(file, reference)
        frequency_list = []
        # write all mutation info in a '_' sep string
        for idx in range(0, len(vcf_dict["#CHROM"])):
            if not vcf_dict["AF"][idx] >= threshold:
                continue
            if scores:
                if vcf_dict['EFF'][idx] is not None:
                    aa_change = vcf_dict['EFF'][idx].split('|')[3]  # extract amino acid changes if provided
                else:
                    aa_change = '-'
                frequency_list.append(
                    (f"{vcf_dict['POS'][idx]}_{vcf_dict['REF'][idx]}_{vcf_dict['ALT'][idx]}_{vcf_dict['MUT_TYPE_'][idx]}_{aa_change}", vcf_dict['AF'][idx])
                )
            else:
                frequency_list.append(
                    (f"{vcf_dict['POS'][idx]}_{vcf_dict['REF'][idx]}_{vcf_dict['ALT'][idx]}_{vcf_dict['MUT_TYPE_'][idx]}", vcf_dict['AF'][idx])
                )

        frequency_lists.append(frequency_list)
    # sort by mutation index
    unique_mutations = sorted(
        {x[0] for li in frequency_lists for x in li}, key=lambda x: int(x.split("_")[0])
    )
    if not unique_mutations:
        sys.exit(f"\033[31m\033[1mERROR:\033[0m No variants to {reference} in all vcf files!")

    return frequency_lists, unique_mutations, file_names


def extract_scores(unique_mutations, scores_file, aa_pos_col, score_col):
    """
    Extract scores from scores_file which corresponding value from aa_pos_col is equal to unique_aa_mutations
    """
    scores_df = pd.read_csv(scores_file)

    # create a dictionary to store the scores for each mutation
    mutation_scores = {}
    for idx, row in scores_df.iterrows():
        mutation_scores[row[aa_pos_col]] = row[score_col]

    unique_scores = []
    for mutation in unique_mutations:
        aa_mut = mutation.split('_')[4]
        if aa_mut in mutation_scores:
            score = mutation_scores[aa_mut]
            unique_scores.append(f"{mutation}_{score}")
        else:
            unique_scores.append(f"{mutation}_nan")

    return unique_scores


def create_freq_array(unique_mutations, frequency_lists):
    """
    create an np array of the mutation frequencies
    """

    frequency_array = []

    for frequency_list in frequency_lists:
        frequencies = []
        for mutation in unique_mutations:
            af = [tup[1] for tup in frequency_list if tup[0] == mutation]
            if af:
                frequencies.append(af[0])
            else:
                frequencies.append(0)
        frequency_array.append(frequencies)

    return np.array(frequency_array)


def annotate_non_covered_regions(coverage_dir, min_coverage, frequency_array, file_names, unique_mutations, reference):
    """
    Insert nan values into np array if position is not covered. Needs
    per base coverage tsv files created by bamqc
    """

    # get tsv files
    per_base_coverage_files = get_files(coverage_dir, "tsv")
    if per_base_coverage_files:
        for i, (file_name, array) in enumerate(zip(file_names, frequency_array)):
            if file_name not in [os.path.splitext(os.path.basename(file))[0] for file in per_base_coverage_files]:
                print(f"\033[31m\033[1mWARNING:\033[0m {file_name} was not found in tsv files.")
                continue
            tsv_file = [file for file in per_base_coverage_files if os.path.splitext(os.path.basename(file))[0] == file_name][0]
            coverage = pd.read_csv(tsv_file, sep="\t")
            coverage = coverage[coverage["#chr"] == reference]
            for j, (mutation, frequency) in enumerate(zip(unique_mutations, array)):
                mut_pos = int(mutation.split("_")[0])
                if coverage[coverage["pos"] == mut_pos].empty or all([frequency == 0, coverage[coverage["pos"] == mut_pos]["coverage"].iloc[0] <= min_coverage]):
                    frequency_array[i][j] = np.NAN

    return np.ma.array(frequency_array, mask=np.isnan(frequency_array))


def delete_common_mutations(frequency_array, unique_mutations):
    """
    delete rows of common mutations (non-zero) that are in the array
    """

    mut_to_del = []

    for idx in range(0, len(frequency_array[0])):
        check_all = []
        for frequency_list in frequency_array:
            check_all.append(frequency_list[idx])
        # check if all mutation in a column are zero (happens with some weird callers)
        if all(x == 0 for x in check_all):
            mut_to_del.append(idx)
        # check if frequencies are present in all columns and the maximal diff is greater than 0.5
        # example [0.8, 0.7, 0.3] is not deleted whereas [0.8, 0.7, 0.7] is deleted
        elif all(x > 0 for x in check_all) and max(check_all)-min(check_all) < 0.5:
            mut_to_del.append(idx)

    for idx in sorted(mut_to_del, reverse=True):
        del unique_mutations[idx]

    return np.delete(frequency_array, mut_to_del, axis=1)


def delete_n_mutations(frequency_array, unique_mutations, min_mut):
    """
    delete mutations that are not present in more than n samples
    """
    mut_to_del = []

    for idx in range(0, len(frequency_array[0])):
        n_mutations = 0
        for frequency_list in frequency_array:
            if frequency_list[idx] > 0:
                n_mutations += 1
        # check if min_mut was reached and if not mark as to delete
        if n_mutations <= min_mut:
            mut_to_del.append(idx)
    # delete the mutations that are found only min_mut times in all samples
    for idx in sorted(mut_to_del, reverse=True):
        del unique_mutations[idx]

    return np.delete(frequency_array, mut_to_del, axis=1)


def zoom_to_genomic_regions(unique_mutations, start_stop):
    """
    restrict the displayed mutations to a user defined genomic range
    """
    zoomed_unique = []

    for mutation in unique_mutations:
        if start_stop[0] <= int(mutation.split("_")[0]) <= start_stop[1]:
            zoomed_unique.append(mutation)

    return zoomed_unique


def parse_gff3(file, reference):
    """
    parse gff3 to dictionary
    """

    gff3_dict = {}

    with open(file, "r") as gff3_file:
        for line in gff3_file:
            # ignore comments and last line
            if not line.startswith(reference):
                continue
            gff_values = line.strip().split("\t")
            # sanity check that the line has a unique ID for the dict key
            # this is a lazy fix as it will exclude e.g. exons without ID and
            # only a parent
            if not gff_values[8].startswith("ID="):
                continue
            # create keys
            if gff_values[2] not in gff3_dict:
                gff3_dict[gff_values[2]] = {}
            # parse the attribute line
            for attribute in gff_values[8].split(";"):
                identifier, val = attribute.split("=")
                # create a new dict for each ID
                if identifier == "ID" and identifier not in gff3_dict:
                    attribute_id = val
                    gff3_dict[gff_values[2]][val] = {}
                # add attributes
                if identifier != "ID":
                    gff3_dict[gff_values[2]][attribute_id][identifier] = val
            # add start, stop and strand
            gff3_dict[gff_values[2]][attribute_id]["start"] = int(gff_values[3])
            gff3_dict[gff_values[2]][attribute_id]["stop"] = int(gff_values[4])
            gff3_dict[gff_values[2]][attribute_id]["strand"] = gff_values[6]

    gff3_file.close()

    if not gff3_dict:
        sys.exit(f"\033[31m\033[1mERROR:\033[0m {reference} not found in gff3 file.")

    return gff3_dict


def get_genome_end(gff3_dict):
    """
    get the end of the genome from the region annotation
    """

    genome_end = 0

    if "region" not in gff3_dict:
        sys.exit("\033[31m\033[1mERROR:\033[0m Region annotation is missing in the gff3!")
    for attribute in gff3_dict["region"].keys():
        stop = gff3_dict["region"][attribute]["stop"]
        if stop > genome_end:
            genome_end = stop

    return genome_end


def create_track_dict(unique_mutations, gff3_info, annotation_type):
    """
    create a dictionary of the genes that have mutations and assess in which
    track these genes should go in case they overlap
    """

    # find genes that have a mutation
    genes_with_mutations = set()
    for mutation in unique_mutations:
        # get the mutation from string
        mutation = int(mutation.split("_")[0])
        for type in annotation_type:
            if type not in gff3_info.keys():
                continue
            for annotation in gff3_info[type]:
                if mutation in range(gff3_info[type][annotation]["start"], gff3_info[type][annotation]["stop"]):
                    if "Name" in gff3_info[type][annotation].keys():
                        attribute_name = gff3_info[type][annotation]["Name"]
                    else:
                        attribute_name = annotation
                    genes_with_mutations.add(
                        (attribute_name,
                         gff3_info[type][annotation]["start"],
                         gff3_info[type][annotation]["stop"],
                         gff3_info[type][annotation]["strand"])
                    )
    if not genes_with_mutations:
        print("\033[31m\033[1mWARNING:\033[0m either the annotation types were not found in gff3 or the mutations are not within genes.")
        return {}, 0

    # create a dict and sort
    gene_dict = {element[0]: [element[1:4]] for element in genes_with_mutations}
    gene_dict = dict(sorted(gene_dict.items(), key=lambda x: x[1][0]))

    # remember for each track the largest stop
    track_stops = [0]

    for gene in gene_dict:
        track = 0
        # check if a start of a gene is smaller than the stop of the current track
        # -> move to new track
        while gene_dict[gene][0][0] < track_stops[track]:
            track += 1
            # if all prior tracks are potentially causing an overlap
            # create a new track and break
            if len(track_stops) <= track:
                track_stops.append(0)
                break
        # in the current track remember the stop of the current gene
        track_stops[track] = gene_dict[gene][0][1]
        # and indicate the track in the dict
        gene_dict[gene].append(track)

    return gene_dict, len(track_stops)
