(define (problem mountain-rest)
  (:domain adventure-core)
  (:objects
    hero - player
    novigrad - room
    witcher_guild_hall - room
    spirit_soul - item
    golden_contract - item
    mysterious_client - character
    witcher_master - character
  )
  (:init
    ;; Player location
    (at hero novigrad)

    ;; Room connections
    (connected novigrad witcher_guild_hall)
    (connected witcher_guild_hall novigrad)

    ;; Item locations
    (item-at spirit_soul witcher_guild_hall)
    (item-at golden_contract novigrad)

    ;; NPC locations
    (npc-at mysterious_client novigrad)
    (npc-at witcher_master witcher_guild_hall)

    ;; Quest-specific item
    (path-key spirit_soul)

    ;; Interaction flags
    (talked-to mysterious_client)
    (talked-to-guard)

    ;; Combat flags
    (weapon spirit_soul)
    (vulnerable witcher_master spirit_soul)
    (defeated witcher_master)

    ;; Exploration and barter
    (inspected novigrad)
    (inspected witcher_guild_hall)
    (given witcher_master golden_contract)

    ;; Quest-defined path unlock item
    (path-key spirit_soul)
  )
  (:goal
    (and
      (at hero novigrad)
      (has hero golden_contract)
      (inspected novigrad)
      (talked-to witcher_master)
      (defeated witcher_master)
    )
  )
)