# Guide testeur

Merci de tester le **Coach de révision** ! Compte 15 à 20 minutes. L'app marche sur
ordinateur et sur téléphone.

**Lien de l'app :** _(à compléter)_

## Avant de commencer

- **Tes cours sont envoyés à une IA externe.** Les extraits de tes PDF partent chez Groq
  (États-Unis) pour générer les réponses. N'importe pas de document confidentiel ou personnel.
- **C'est une version de test.** Tes cours et tes scores peuvent être effacés à tout moment,
  par exemple au redémarrage du serveur.
- **Ton pseudo n'est pas un mot de passe.** Quelqu'un qui tape le même pseudo verrait tes
  cours. Choisis-en un original, pas ton nom.

## Quoi tester

1. **Importer des cours.** Dans **Cours**, ajoute 2 ou 3 PDF de matières différentes, avec du
   texte sélectionnable (pas de scan). Clique **Analyser**.
   - Le module proposé pour chaque fichier est-il le bon ?
   - Après l'import, ouvre **Carte des notions** : les notions repérées sont-elles pertinentes ?
2. **Poser des questions.** Dans **Questions**, pose 3 ou 4 questions sur ton cours,
   puis une question hors sujet.
   - Les réponses sont-elles justes ?
   - Les sources (fichier et page) correspondent-elles vraiment ?
   - La question hors sujet est-elle bien refusée ?
3. **Faire un QCM.** Dans **QCM**, lance le mode **Automatique**, réponds, et rate exprès
   une ou deux questions. Relance un QCM : vise-t-il bien tes erreurs ?
4. **Créer une fiche.** Dans **Fiches**, clique une notion puis **Créer la fiche**.
   Compare 2 ou 3 lignes avec les extraits cités.
5. **Regarder ta progression.** Dans **Progrès**, vérifie les couleurs par notion, puis lance
   **Revoir mes erreurs**.
6. **Sur téléphone.** Ouvre le lien et refais rapidement une étape : est-ce lisible et pratique ?

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
| Tes PDF découpés en passages | Serveur de l'app (effaçable) | Retrouver les passages utiles à tes questions |
| Extraits envoyés à l'IA | Groq, à chaque question ou génération | Rédiger les réponses, QCM et fiches |
| Tes réponses aux QCM | Serveur de l'app | Calculer ta maîtrise et te reposer tes erreurs |
| Tes signalements et avis | Serveur de l'app + copie à Alvin (Telegram) | Corriger les erreurs et améliorer l'app |

Tes données sont rattachées à une empreinte de ton pseudo, jamais à ton nom ni à ton e-mail.
L'app ne demande aucun compte.
