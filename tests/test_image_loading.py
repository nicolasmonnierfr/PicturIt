"""Tests de la lecture d'images : en-tête, vignette EXIF, décodage, tolérance.

Chemin le plus sensible de l'application : c'est lui qui a produit 17,5 % de
photos faussement « illisibles », puis les gains de performance sur partage
réseau. Il n'était vérifié que par des scripts jetables.

``QImage`` ne réclame pas de ``QApplication`` : ces tests restent donc conformes
à la règle du harnais (aucune fenêtre créée).
"""

from __future__ import annotations

import io
import os
from datetime import datetime

import piexif
import pytest
from PIL import Image

from core import imaging, metadata, thumbnails

from .conftest import pad_to, write_photo

_DATE = datetime(2023, 7, 15, 10, 20, 30)
_GPS = (48.8584, 2.2945)


def _vignette_jpeg(taille: tuple[int, int], couleur=(200, 30, 30)) -> bytes:
    """Fabrique les octets JPEG d'une vignette embarquable."""
    tampon = io.BytesIO()
    Image.new("RGB", taille, couleur).save(tampon, "JPEG", quality=70)
    return tampon.getvalue()


def _photo_avec_vignette(
    path, dimensions=(1200, 900), vignette=(160, 120), orientation=None
) -> str:
    """Photo dont l'EXIF contient une vignette, comme en produisent les appareils."""
    chemin = write_photo(path, dt=_DATE, gps=_GPS, size=dimensions)
    exif = piexif.load(chemin)
    exif["thumbnail"] = _vignette_jpeg(vignette)
    exif["1st"] = {piexif.ImageIFD.Compression: 6}
    if orientation is not None:
        exif["0th"][piexif.ImageIFD.Orientation] = orientation
    piexif.insert(piexif.dump(exif), chemin)
    return chemin


class TestConfigurationPillow:
    """``core.imaging`` est importé par tous les chemins de lecture."""

    def test_images_tronquees_acceptees(self):
        """Sans ce réglage, 17,5 % des photos d'un dossier réel étaient rejetées."""
        from PIL import ImageFile

        assert ImageFile.LOAD_TRUNCATED_IMAGES is True

    def test_support_heic_annonce(self):
        assert isinstance(imaging.HEIF_SUPPORTED, bool)


class TestLectureEnTete:
    """``read_header`` : une seule lecture qui servira à tout."""

    def test_lit_le_debut_du_fichier(self, tmp_path):
        chemin = write_photo(tmp_path / "a.jpg", size=(64, 48))
        entete = thumbnails.read_header(chemin)
        assert entete is not None
        assert entete.startswith(b"\xff\xd8")  # marqueur JPEG

    def test_ne_lit_pas_tout_le_fichier(self, tmp_path):
        """Sur un gros fichier, on ne transfère que l'en-tête.

        Le fichier est gonflé explicitement : une image de test est un aplat de
        couleur, qui se compresse trop bien pour dépasser le seuil par sa seule
        définition.
        """
        chemin = write_photo(tmp_path / "grosse.jpg", size=(800, 600))
        pad_to(chemin, thumbnails._EXIF_HEAD_BYTES * 3)

        assert os.path.getsize(chemin) > thumbnails._EXIF_HEAD_BYTES
        assert len(thumbnails.read_header(chemin)) == thumbnails._EXIF_HEAD_BYTES

    def test_petit_fichier_lu_entierement(self, tmp_path):
        chemin = write_photo(tmp_path / "petite.jpg", size=(32, 24))
        assert len(thumbnails.read_header(chemin)) == os.path.getsize(chemin)

    def test_fichier_absent(self, tmp_path):
        assert thumbnails.read_header(str(tmp_path / "absent.jpg")) is None


class TestVignetteEmbarquee:
    """``thumbnail_from_header`` : quelques Ko au lieu de plusieurs Mo."""

    def test_extraction(self, tmp_path):
        chemin = _photo_avec_vignette(tmp_path / "a.jpg")
        entete = thumbnails.read_header(chemin)
        image = thumbnails.thumbnail_from_header(entete, 160)
        assert image is not None
        assert max(image.width(), image.height()) == 160

    def test_absente_si_pas_de_vignette(self, tmp_path):
        """Photo re-compressée : pas de vignette, l'appelant relira le fichier."""
        chemin = write_photo(tmp_path / "nue.jpg", size=(800, 600))
        entete = thumbnails.read_header(chemin)
        assert thumbnails.thumbnail_from_header(entete, 160) is None

    def test_refus_si_trop_petite(self, tmp_path):
        """Agrandir une vignette de 160 px vers 320 donnerait un résultat flou."""
        chemin = _photo_avec_vignette(tmp_path / "a.jpg", vignette=(160, 120))
        entete = thumbnails.read_header(chemin)
        assert thumbnails.thumbnail_from_header(entete, 320) is None

    @pytest.mark.parametrize(
        "orientation,portrait",
        [(1, False), (3, False), (6, True), (8, True)],
    )
    def test_orientation_appliquee(self, tmp_path, orientation, portrait):
        """La vignette EXIF n'a pas son propre EXIF : l'orientation vient du parent.

        Sans cela, les photos prises en portrait s'afficheraient couchées.
        """
        chemin = _photo_avec_vignette(
            tmp_path / "a.jpg", vignette=(160, 120), orientation=orientation
        )
        image = thumbnails.thumbnail_from_header(thumbnails.read_header(chemin), 160)
        assert image is not None
        assert (image.height() > image.width()) is portrait

    @pytest.mark.parametrize("entete", [b"", b"pas une image", b"\xff\xd8 tronque"])
    def test_entete_invalide(self, entete):
        assert thumbnails.thumbnail_from_header(entete, 160) is None


class TestDecodage:
    """``load_qimage`` : le repli quand la vignette embarquée manque."""

    def test_vignette_a_la_taille_demandee(self, tmp_path):
        chemin = write_photo(tmp_path / "a.jpg", size=(800, 600))
        image = thumbnails.load_qimage(chemin, 160)
        assert image is not None
        assert max(image.width(), image.height()) == 160

    def test_image_complete_sans_taille(self, tmp_path):
        chemin = write_photo(tmp_path / "a.jpg", size=(320, 240))
        image = thumbnails.load_qimage(chemin)
        assert (image.width(), image.height()) == (320, 240)

    def test_fichier_corrompu(self, tmp_path):
        chemin = tmp_path / "corrompu.jpg"
        chemin.write_bytes(b"ceci n'est pas une image")
        assert thumbnails.load_qimage(str(chemin), 160) is None

    def test_fichier_vide(self, tmp_path):
        chemin = tmp_path / "vide.jpg"
        chemin.write_bytes(b"")
        assert thumbnails.load_qimage(str(chemin), 160) is None

    def test_fichier_absent(self, tmp_path):
        assert thumbnails.load_qimage(str(tmp_path / "absent.jpg"), 160) is None

    def test_jpeg_tronque_reste_lisible(self, tmp_path):
        """Le cas qui rendait 17,5 % des photos « illisibles ».

        On ampute la fin du fichier : Pillow refuserait de décoder sans
        ``LOAD_TRUNCATED_IMAGES``, alors que l'image est exploitable.
        """
        chemin = write_photo(tmp_path / "a.jpg", size=(400, 300))
        with open(chemin, "rb") as fh:
            octets = fh.read()
        with open(chemin, "wb") as fh:
            fh.write(octets[: int(len(octets) * 0.85)])

        assert thumbnails.load_qimage(chemin, 160) is not None


class TestRecolteDesMetadonnees:
    """``collect_metadata`` évite de rouvrir le fichier juste pour son EXIF."""

    def test_identique_a_une_lecture_dediee(self, tmp_path):
        chemin = write_photo(
            tmp_path / "a.jpg", dt=_DATE, gps=_GPS, size=(640, 480)
        )
        attendu = metadata.read(chemin)
        metadata.clear_cache()

        thumbnails.load_qimage(chemin, 160, collect_metadata=True)
        obtenu = metadata.read(chemin)

        assert (obtenu.width, obtenu.height) == (attendu.width, attendu.height)
        assert obtenu.datetime_original == attendu.datetime_original
        assert obtenu.latitude == pytest.approx(attendu.latitude)
        assert obtenu.longitude == pytest.approx(attendu.longitude)

    def test_dimensions_reelles_malgre_draft(self, tmp_path):
        """Piège : ``draft`` décode à échelle réduite et change ``img.size``.

        Les métadonnées doivent être lues **avant**, sinon on enregistrerait la
        taille de la vignette à la place de celle de la photo.
        """
        chemin = write_photo(tmp_path / "grande.jpg", size=(1600, 1200))
        metadata.clear_cache()
        thumbnails.load_qimage(chemin, 160, collect_metadata=True)
        meta = metadata.read(chemin)
        assert (meta.width, meta.height) == (1600, 1200)

    def test_sans_collecte_rien_n_est_mis_en_cache(self, tmp_path):
        chemin = write_photo(tmp_path / "a.jpg", dt=_DATE)
        metadata.clear_cache()
        thumbnails.load_qimage(chemin, 160)
        assert metadata._cache == {}

    def test_photo_sans_exif(self, tmp_path):
        chemin = write_photo(tmp_path / "nue.jpg", size=(200, 150))
        metadata.clear_cache()
        thumbnails.load_qimage(chemin, 160, collect_metadata=True)
        meta = metadata.read(chemin)
        assert (meta.width, meta.height) == (200, 150)
        assert meta.datetime_original is None


class TestFichierVide:
    """Garde-fou avant de lancer ffmpeg sur un fichier de 0 octet."""

    def test_fichier_avec_contenu(self, tmp_path):
        chemin = write_photo(tmp_path / "a.jpg")
        assert thumbnails._has_content(chemin) is True

    def test_fichier_vide(self, tmp_path):
        chemin = tmp_path / "vide.jpg"
        chemin.write_bytes(b"")
        assert thumbnails._has_content(str(chemin)) is False

    def test_fichier_absent(self, tmp_path):
        assert thumbnails._has_content(str(tmp_path / "absent.jpg")) is False
