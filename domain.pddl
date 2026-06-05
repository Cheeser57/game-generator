
(define (domain adventure-core)

    (:requirements :strips :typing)

    (:types
        player
        room
        item
        character
    )

    (:predicates

        ;; player location
        (at ?p - player ?r - room)

        ;; room connections
        (connected ?from - room ?to - room)

        ;; item location
        (item-at ?item - item ?r - room)

        ;; inventory
        (has ?p - player ?item - item)

        ;; npc location
        (npc-at ?c - character ?r - room)

        ;; interaction flags
        (talked-to ?c - character)
        (talked-to-guard)

        ;; combat flags
        (weapon ?item - item)
        (vulnerable ?c - character ?item - item)
        (defeated ?c - character)

        ;; exploration and barter
        (inspected ?r - room)
        (given ?c - character ?item - item)

        ;; quest-defined path unlock item
        (path-key ?item - item)

    )

    ;; =====================================================
    ;; MOVE
    ;; =====================================================

    (:action move

        :parameters (
            ?p - player
            ?from - room
            ?to - room
        )

        :precondition
            (and
                (at ?p ?from)
                (connected ?from ?to)
            )

        :effect
            (and
                (not (at ?p ?from))
                (at ?p ?to)
            )
    )

    ;; =====================================================
    ;; TAKE
    ;; =====================================================

    (:action take

        :parameters (
            ?p - player
            ?item - item
            ?r - room
        )

        :precondition
            (and
                (at ?p ?r)
                (item-at ?item ?r)
            )

        :effect
            (and
                (not (item-at ?item ?r))
                (has ?p ?item)
            )
    )

    ;; =====================================================
    ;; DROP
    ;; =====================================================

    (:action drop

        :parameters (
            ?p - player
            ?item - item
            ?r - room
        )

        :precondition
            (and
                (at ?p ?r)
                (has ?p ?item)
            )

        :effect
            (and
                (not (has ?p ?item))
                (item-at ?item ?r)
            )
    )

    ;; =====================================================
    ;; TALK
    ;; =====================================================

    (:action talk

        :parameters (
            ?p - player
            ?c - character
            ?r - room
        )

        :precondition
            (and
                (at ?p ?r)
                (npc-at ?c ?r)
            )

        :effect
            (and
                (talked-to ?c)
                (talked-to-guard)
            )
    )

    ;; =====================================================
    ;; GIVE
    ;; =====================================================

    (:action give

        :parameters (
            ?p - player
            ?c - character
            ?item - item
            ?r - room
        )

        :precondition
            (and
                (at ?p ?r)
                (npc-at ?c ?r)
                (has ?p ?item)
                (talked-to ?c)
            )

        :effect
            (and
                (not (has ?p ?item))
                (given ?c ?item)
            )
    )

    ;; =====================================================
    ;; INSPECT
    ;; =====================================================

    (:action inspect

        :parameters (
            ?p - player
            ?r - room
        )

        :precondition
            (at ?p ?r)

        :effect
            (inspected ?r)
    )

    ;; =====================================================
    ;; ATTACK
    ;; =====================================================

    (:action attack

        :parameters (
            ?p - player
            ?c - character
            ?weapon - item
            ?r - room
        )

        :precondition
            (and
                (at ?p ?r)
                (npc-at ?c ?r)
                (has ?p ?weapon)
                (weapon ?weapon)
                (vulnerable ?c ?weapon)
            )

        :effect
            (and
                (not (npc-at ?c ?r))
                (defeated ?c)
            )
    )

    ;; =====================================================
    ;; UNLOCK PATH
    ;; =====================================================

    (:action unlock-path

        :parameters (
            ?p - player
            ?item - item
            ?from - room
            ?to - room
        )

        :precondition
            (and
                (at ?p ?from)
                (has ?p ?item)
                (path-key ?item)
            )

        :effect
            (and
                (connected ?from ?to)
                (connected ?to ?from)
            )
    )
)
