# Guide testeur

Merci de tester le **Coach de révision** ! Compte 15 à 20 minutes. L'app marche sur
ordinateur et sur téléphone.

**Lien de l'app :** https://amonnnn.streamlit.app

## Avant de commencer

- **Tes cours sont envoyés à une IA externe.** Tes questions et les extraits de tes PDF partent
  chez Groq (États-Unis) pour générer les réponses. N'importe pas de document confidentiel ou personnel.
- **Recherche web.** Quand l'assistant cherche sur le web, ta question (reformulée) est envoyée
  à Tavily, un moteur de recherche. N'y mets pas d'information personnelle.
  Limite : 10 recherches web par jour pour toi, et 30 par jour pour toute l'app (offre gratuite).
- **C'est une version de test.** Tes cours et ta progression sont conservés d'une visite à
  l'autre, mais la base pourra être réinitialisée si besoin.
- **Connexion avec ton compte Google.** L'app ne voit jamais ton mot de passe : elle reçoit
  seulement ton e-mail et ton nom. Tu retrouves tes cours sur ordinateur comme sur téléphone.
- **Tu avais déjà testé avec un pseudo ?** Dans la barre latérale, ouvre **Récupérer mes
  données d'avant** et tape ton ancien pseudo : tes cours et ta progression passent sur ton compte.

## Quoi tester

1. **Importer des cours.** Dans **Cours**, ajoute 2 ou 3 PDF de matières différentes, avec du
   texte sélectionnable (pas de scan). Clique **Analyser**.
   - Le module proposé pour chaque fichier est-il le bon ?
   - Après l'import, ouvre **Carte des notions** : les notions repérées sont-elles pertinentes ?
2. **Discuter avec l'assistant.** Dans **Questions**, chaque réponse porte une étiquette :
   « D'après ton cours », « D'après le web » ou « Réponse générale, sans source ».
   - **Sur ton cours** : pose 3 ou 4 questions. Les réponses sont-elles justes ? Les sources
     (fichier et page) correspondent-elles vraiment ?
   - **Question de suivi** courte (« et sa formule ? », « donne un exemple ») : la ligne
     « Compris comme » reprend-elle bien ta question ? La réponse suit-elle la conversation ?
   - **Un fait hors cours** (une date, un chiffre, une définition précise, une actualité) :
     l'étiquette passe-t-elle à « D'après le web » ? Ouvre **Sources web** : les liens
     marchent-ils, les sites sont-ils fiables, la réponse dit-elle bien ce qu'ils disent ?
   - **Une discussion** (« salut », « comment m'organiser pour réviser ? », « explique plus
     simplement ») : la réponse est-elle utile, et bien étiquetée « sans source » ?
   - **Le bouton « Chercher sur le web »** : pose une question de cours que tes PDF ne traitent
     pas. Sous « Pas trouvé dans ton cours », clique le bouton : la question est-elle relancée
     sur le web ?
   - Signale dans **Avis** toute réponse mal classée (ex. une question de cours traitée par le web).
3. **Faire un QCM.** Dans **QCM**, lance le mode **Automatique**, réponds, et rate exprès
   une ou deux questions. Relance un QCM : vise-t-il bien tes erreurs ?
4. **Créer une fiche.** Dans **Fiches**, clique une notion puis **Créer la fiche**.
   Compare 2 ou 3 lignes avec les extraits cités. Recharge la page : la fiche est-elle toujours
   dans **Mes fiches** ? Ouvre **Nouvelle fiche** et redemande la même notion : l'app doit te
   montrer la fiche enregistrée au lieu d'en refaire une. Essaie aussi **Régénérer** et **Supprimer**.
   Importer un nouveau fichier dans le module retire les fiches marquées « pas dans le cours »
   (la notion y est peut-être maintenant) : refais-les.
5. **Regarder ta progression.** Dans **Progrès**, vérifie les couleurs par notion, puis lance
   **Revoir mes erreurs**.
6. **Sur téléphone.** Ouvre le lien, connecte-toi avec le même compte Google : retrouves-tu
   tes cours ? Refais rapidement une étape : est-ce lisible et pratique ?

## Signaler un problème

- **Une question de QCM fausse, ambiguë ou inventée** : clique **Signaler une erreur** sous
  la question et choisis le motif.
- **Un bug, une idée ou un avis général** : page **Avis**. Pour un bug, décris ce que tu
  faisais, ce qui s'est passé et ce que tu attendais.
- **L'app ne répond plus** : si elle affiche « limite de l'API atteinte », attends une minute
  et réessaie (on utilise une offre gratuite). Sinon, préviens Alvin directement.

## Ce qui est enregistré

| Donnée | Où | Pourquoi |
|---|---|---|
| Ton e-mail et ton nom (compte Google) | Base de données de l'app (Supabase) | T'identifier et séparer tes cours de ceux des autres |
| Tes PDF découpés en passages | Base de données de l'app (Supabase) | Retrouver les passages utiles à tes questions |
| Extraits envoyés à l'IA | Groq, à chaque question ou génération | Rédiger les réponses, QCM et fiches |
| Ta question reformulée (recherche web) | Tavily, seulement quand l'assistant cherche sur le web | Trouver des sources ; les résultats sont ensuite envoyés à Groq pour rédiger la réponse |
| Date et heure de tes recherches web (sans la question) | Base de données de l'app, effacées après 2 jours | Limiter les recherches par jour : 10 par personne, 30 pour toute l'app (quota gratuit partagé) |
| Tes conversations du chat (avec leurs sources, liens web compris) | Base de données de l'app (Supabase) | Les retrouver et comprendre tes questions de suivi (« Nouvelle conversation » les efface) |
| Tes fiches de révision | Base de données de l'app (Supabase) | Les retrouver sans refaire appel à l'IA (supprimables dans **Fiches**, retirées avec le fichier dont elles viennent) |
| Tes réponses aux QCM | Base de données de l'app (Supabase) | Calculer ta maîtrise et te reposer tes erreurs |
| Tes signalements et avis | Base de données de l'app + copie à Alvin (Telegram) | Corriger les erreurs et améliorer l'app |

Tes cours, réponses et avis sont rattachés à un identifiant de compte anonyme (une empreinte
de ton identifiant Google), pas directement à ton e-mail. Personne d'autre ne peut voir tes cours.
