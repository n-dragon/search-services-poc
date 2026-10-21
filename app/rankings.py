"""Closed ranking catalog and auditable decomposition of real Vespa features."""
import math
from typing import Literal

Ranking = Literal['lexical', 'lexical-balanced', 'lexical-title',
    'lexical-saturated', 'lexical-coverage', 'lexical-proximity', 'unranked']

PROFILES = {
    'lexical': {
        'title': 'BM25 · titre × 2', 'formula': '2 × T + B',
        'description': 'Référence : le score du titre pèse deux fois celui du texte.',
        'tradeoff': 'Le poids 2 est une heuristique, sans gain de pertinence démontré.',
        'phase': 'first', 'rerank_count': None},
    'lexical-balanced': {
        'title': 'BM25 · équilibré', 'formula': 'T + B',
        'description': 'Même poids pour les deux scores BM25.',
        'tradeoff': 'Les distributions de scores du titre et du texte peuvent être différentes.',
        'phase': 'first', 'rerank_count': None},
    'lexical-title': {
        'title': 'BM25 · titre renforcé', 'formula': '4 × T + B',
        'description': 'Donne davantage de poids aux correspondances dans le titre.',
        'tradeoff': 'Peut déclasser un document utile dont seul le texte répond à la question.',
        'phase': 'first', 'rerank_count': None},
    'lexical-saturated': {
        'title': 'BM25 · contributions saturées', 'formula': '2 × T / (8 + T) + B / (8 + B)',
        'description': 'Sature séparément les deux scores : une contribution extrême domine moins le total.',
        'tradeoff': 'Score dans [0, 3[ pour des scores BM25 positifs ; ce n’est pas une probabilité. Le seuil 8 reste à évaluer.',
        'phase': 'first', 'rerank_count': None},
    'lexical-coverage': {
        'title': 'BM25 + couverture', 'formula': '(2 × T + B) × (0,5 + 0,5 × C)',
        'description': 'Favorise les candidats qui retrouvent davantage de termes de la requête dans un même champ.',
        'tradeoff': 'C est le maximum des couvertures titre/texte, pas leur union. Un document répartissant les termes entre les deux champs peut être pénalisé.',
        'phase': 'second', 'rerank_count': 50},
    'lexical-proximity': {
        'title': 'BM25 + proximité', 'formula': '2 × T + B + 2 × Pₜ + Pᵦ',
        'description': 'Ajoute un bonus lorsque plusieurs termes se suivent dans le même champ.',
        'tradeoff': 'Aucun bonus avec moins de deux termes trouvés dans le champ. L’ordre et la segmentation influencent le bonus ; ce n’est pas une recherche de phrase exacte.',
        'phase': 'second', 'rerank_count': 50},
    'unranked': {
        'title': 'Brut · sans classement', 'formula': 'Aucun score de pertinence',
        'description': 'Même recherche lexicale et même filtre de source ; les correspondances sont rendues sans calcul de pertinence.',
        'tradeoff': 'Ordre interne non garanti : ce top peut changer avec l’index. Ce mode conserve l’analyse linguistique et au moins un terme correspondant ; ce n’est pas une recherche de sous-chaîne.',
        'phase': 'none', 'rerank_count': None},
}

DEFINITIONS = {
    'T': 'bm25(title), avec k1 = 1,2 et b = 0,75',
    'B': 'bm25(body), avec k1 = 1,2 et b = 0,75',
    'C': 'max(fieldMatch(title).queryCompleteness, fieldMatch(body).queryCompleteness)',
    'P': '0 si fieldMatch(champ).matches < 2 ; sinon unweightedProximity × relatedness × queryCompleteness de ce champ',
}


def catalog():
    return {'profiles': [{'id': key, **value} for key, value in PROFILES.items()],
        'comparison_baseline': 'unranked',
        'definitions': DEFINITIONS,
        'limits': 'Les profils à deux phases reclassent les 50 meilleurs candidats BM25 par nœud (un seul nœud ici). Ils ne parcourent pas de nouveau le corpus. Scores comparables à l’intérieur d’un profil et d’une requête, pas entre formules. Aucun profil n’est validé comme plus pertinent.'}


def explain(profile, features, actual_score):
    """Never replace missing engine features with fabricated zero contributions."""
    if profile not in PROFILES:
        raise ValueError('Unknown ranking profile')
    if profile == 'unranked':
        return {'available': False, 'formula': PROFILES[profile]['formula'],
            'reason': 'Mode brut : aucun score de pertinence calculé. L’ordre interne n’est pas garanti.'}
    required = ['bm25(title)', 'bm25(body)']
    if profile == 'lexical-coverage':
        required += ['query_coverage']
    if profile == 'lexical-proximity':
        required += ['title_proximity', 'body_proximity']
    if any(not isinstance(features.get(key), (float, int)) or not math.isfinite(features[key]) for key in required):
        return {'available': False, 'formula': PROFILES[profile]['formula'],
            'reason': 'Contributions moteur manquantes ou non finies ; aucune valeur remplacée par zéro.'}
    title, body = features['bm25(title)'], features['bm25(body)']
    weight = 1 if profile == 'lexical-balanced' else 4 if profile == 'lexical-title' else 2
    if profile == 'lexical-saturated':
        contributions = [('Titre saturé : 2 × T / (8 + T)', 2*title/(8+title)),
            ('Texte saturé : B / (8 + B)', body/(8+body))]
    else:
        contributions = [(f'Titre : {weight} × T', weight*title), ('Texte : B', body)]
        if profile == 'lexical-coverage':
            contributions.append(('Correction de couverture : (2 × T + B) × (C − 1) / 2',
                (2*title+body)*(features['query_coverage']-1)/2))
        elif profile == 'lexical-proximity':
            contributions += [('Proximité du titre : 2 × Pₜ', 2*features['title_proximity']),
                ('Proximité du texte : Pᵦ', features['body_proximity'])]
    computed = sum(value for _, value in contributions)
    matches = isinstance(actual_score, (float, int)) and math.isclose(computed, actual_score, rel_tol=1e-5, abs_tol=1e-6)
    return {'available': True, 'formula': PROFILES[profile]['formula'],
        'contributions': [{'label': label, 'value': value} for label, value in contributions],
        'computed_score': computed, 'matches_score': matches,
        'note': None if matches else 'Le score renvoyé ne correspond pas à la décomposition ; il peut notamment avoir été ajusté par Vespa hors des candidats reclassés.'}
