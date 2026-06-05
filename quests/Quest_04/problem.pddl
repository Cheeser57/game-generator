(define (problem choice-of-gold)
  (:domain adventure-core)
  (:objects
    hero - player
    witcher_guild_hall - room
    royal_palace - room
    witcher_sigil - item
    mountain_whisper - item
    witcher_master - character
    king_of_novigrad - character
  )
  (:init
    ;; Player location
    (at hero witcher_guild_hall)

    ;; Room connections
    (connected witcher_guild_hall royal_palace)
    (connected royal_palace witcher_guild_hall)

    ;; Items in rooms
    (item-at witcher_sigil witcher_guild_hall)
    (item-at mountain_whisper royal_palace)

    ;; NPC locations
    (npc-at witcher_master witcher_guild_hall)
    (npc-at king_of_novigrad royal_palace)

    ;; Inventory
    (has hero witcher_sigil)

    ;; Interaction flags
    (talked-to witcher_master)
    (talked-to-guard)

    ;; Combat flags
    (weapon witcher_sigil)
    (vulnerable king_of_novigrad witcher_sigil)

    ;; Exploration and barter
    (inspected witcher_guild_hall)
    (inspected royal_palace)

    ;; Quest-specific items
    (path-key mountain_whisper)

    ;; Quest-defined path unlock item
    (path-key mountain_whisper)
  )
  (:goal
    (and
      (at hero royal_palace)
      (has hero mountain_whisper)
      (talked-to king_of_novigrad)
      (defeated king_of_novigrad)
      (inspected royal_palace)
    )
  )
)